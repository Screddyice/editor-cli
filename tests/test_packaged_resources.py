import subprocess
import zipfile
from importlib.resources import as_file, files
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    output = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    return next(output.glob("*.whl"))


def wheel_names(wheel: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(wheel) as archive:
        return tuple(archive.namelist())


def test_distribution_contains_native_sources_and_skill(built_wheel):
    names = set(wheel_names(built_wheel))
    assert "editor_cli/resources/native/Package.swift" in names
    assert "editor_cli/resources/skills/final-cut-editor/SKILL.md" in names
    assert "editor_cli/resources/canary/fcp_live_canary.py" in names


def test_wheel_resources_match_development_copies():
    root = Path.cwd()
    packaged = files("editor_cli.resources")
    pairs = (
        (
            packaged.joinpath("skills/final-cut-editor/SKILL.md"),
            root / "skills/final-cut-editor/SKILL.md",
        ),
        (
            packaged.joinpath("native/Package.swift"),
            root / "native/final-cut-bridge/Package.swift",
        ),
        (
            packaged.joinpath("native/Sources/FinalCutBridge/Protocol.swift"),
            root / "native/final-cut-bridge/Sources/FinalCutBridge/Protocol.swift",
        ),
        (
            packaged.joinpath("native/Tests/FinalCutBridgeTests/ProtocolTests.swift"),
            root
            / "native/final-cut-bridge/Tests/FinalCutBridgeTests/ProtocolTests.swift",
        ),
        (
            packaged.joinpath("canary/fcp_live_canary.py"),
            root / "scripts/fcp_live_canary.py",
        ),
    )
    for resource, development in pairs:
        with as_file(resource) as packaged_path:
            assert packaged_path.is_file()
            assert packaged_path.read_bytes() == development.read_bytes()
