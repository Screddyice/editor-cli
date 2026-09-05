from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = REPO_ROOT / "src/editor_cli/resources"


def _files_below(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _required_development_resources() -> dict[str, bytes]:
    native = REPO_ROOT / "native/final-cut-bridge"
    required = {
        f"native/{name}": content
        for name, content in _files_below(native).items()
        if not name.startswith(".build/")
    }
    required["skills/final-cut-editor/SKILL.md"] = (
        REPO_ROOT / "skills/final-cut-editor/SKILL.md"
    ).read_bytes()
    required["canary/fcp_live_canary.py"] = (
        REPO_ROOT / "scripts/fcp_live_canary.py"
    ).read_bytes()
    return required


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory):
    output = tmp_path_factory.mktemp("distributions")
    subprocess.run(
        ["uv", "build", "--out-dir", str(output)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return next(output.glob("*.whl")), next(output.glob("*.tar.gz"))


def test_packaged_resource_tree_matches_every_reviewed_development_file():
    packaged = _files_below(RESOURCE_ROOT)
    packaged.pop("__init__.py")
    assert packaged == _required_development_resources()


def test_wheel_contains_every_packaged_resource_byte(built_distributions):
    wheel, _ = built_distributions
    expected = _files_below(RESOURCE_ROOT)
    prefix = "editor_cli/resources/"
    with zipfile.ZipFile(wheel) as archive:
        packaged = {
            name.removeprefix(prefix): archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    assert packaged == expected


def test_sdist_contains_every_packaged_resource_byte(built_distributions):
    _, sdist = built_distributions
    expected = _files_below(RESOURCE_ROOT)
    prefix = "editor_cli-0.1.0/src/editor_cli/resources/"
    with tarfile.open(sdist) as archive:
        packaged = {
            member.name.removeprefix(prefix): archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile() and member.name.startswith(prefix)
        }
    assert packaged == expected


def test_installed_wheel_resolves_resources_without_checkout(
    built_distributions, tmp_path
):
    wheel, _ = built_distributions
    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--python",
            str(venv / "bin/python"),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            str(venv / "bin/python"),
            "-c",
            (
                "from editor_cli.resources import final_cut_skill, live_canary, "
                "native_source; "
                "assert native_source().joinpath('Package.swift').is_file(); "
                "assert final_cut_skill().joinpath('SKILL.md').is_file(); "
                "assert live_canary().is_file()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
