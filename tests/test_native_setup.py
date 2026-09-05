from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from editor_cli.setup import SetupError, SetupPaths, run_setup


class FakePlatform:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.python: Path | None = None
        self.signature_valid = True

    def run(self, command: tuple[str, ...], *, cwd: Path | None = None) -> None:
        self.commands.append(command)
        if command[:2] == ("swift", "build"):
            assert cwd is None
            source = Path(command[command.index("--package-path") + 1])
            assert source.stat().st_mode & 0o777 == 0o700
            binary = source / ".build" / "release" / "FinalCutBridge"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"reviewed native helper")

    def commands_contain(self, expected: list[str]) -> bool:
        return any(
            all(item in command for item in expected) for command in self.commands
        )

    def install_watch(self, paths: SetupPaths) -> None:
        for root in (paths.codex_skills, paths.claude_skills):
            skill = root / "watch"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(
                "---\nname: watch\nversion: 0.2.0\n---\n",
                encoding="utf-8",
            )

    def verify_mcp(self, python: Path, _repo_root: Path) -> bool:
        self.python = python
        return True

    def helper_has_signature(self, _path: Path, _identifier: str) -> bool:
        return self.signature_valid


def setup_paths(tmp_path: Path) -> SetupPaths:
    return SetupPaths(
        repo_root=Path.cwd(),
        codex_config=tmp_path / "codex" / "config.toml",
        claude_config=tmp_path / "claude.json",
        codex_skills=tmp_path / "codex" / "skills",
        claude_skills=tmp_path / "claude" / "skills",
        application_support=tmp_path / "Library/Application Support/Editor CLI",
    )


def test_setup_builds_and_signs_stable_helper(tmp_path):
    platform = FakePlatform()

    result = run_setup(setup_paths(tmp_path), platform=platform)

    helper = tmp_path / "Library/Application Support/Editor CLI/bin/editor-fcp-bridge"
    assert helper in result.changed
    assert helper.read_bytes() == b"reviewed native helper"
    assert helper.stat().st_mode & 0o777 == 0o700
    assert platform.commands_contain(["swift", "build", "-c", "release"])
    assert platform.commands_contain(["codesign", "--force", "--sign", "-"])


def test_setup_persists_signed_helper_metadata(tmp_path):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)

    run_setup(paths, platform=platform)

    helper = paths.application_support / "bin/editor-fcp-bridge"
    metadata = json.loads(helper.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["protocol_version"] == 1
    assert metadata["sha256"] == hashlib.sha256(helper.read_bytes()).hexdigest()
    assert len(metadata["source_sha256"]) == 64


def test_setup_skips_rebuild_when_packaged_source_and_helper_match(tmp_path):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)

    run_setup(paths, platform=platform)
    first_command_count = len(platform.commands)
    second = run_setup(paths, platform=platform)

    assert second.changed == []
    assert len(platform.commands) == first_command_count


@pytest.mark.parametrize("collision", ["helper", "metadata", "helper_symlink"])
def test_setup_rejects_unmanaged_native_artifact_before_changes(tmp_path, collision):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    helper = paths.application_support / "bin/editor-fcp-bridge"
    metadata = helper.with_suffix(".json")
    helper.parent.mkdir(parents=True)
    if collision == "helper":
        helper.write_bytes(b"unmanaged")
    elif collision == "metadata":
        metadata.write_text("{}\n", encoding="utf-8")
    else:
        target = tmp_path / "unmanaged-helper"
        target.write_bytes(b"unmanaged")
        helper.symlink_to(target)

    with pytest.raises(SetupError, match="unmanaged native helper"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.codex_config.exists()
    assert not paths.claude_config.exists()


def test_setup_rejects_metadata_that_lacks_matching_helper_signature(tmp_path):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    run_setup(paths, platform=platform)
    platform.commands.clear()
    platform.signature_valid = False

    with pytest.raises(SetupError, match="unmanaged native helper"):
        run_setup(paths, platform=platform)

    assert platform.commands == []


@pytest.mark.parametrize("artifact", ["helper", "metadata"])
def test_setup_rejects_symlinked_managed_native_artifact(tmp_path, artifact):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    run_setup(paths, platform=platform)
    helper = paths.application_support / "bin/editor-fcp-bridge"
    metadata = helper.with_suffix(".json")
    selected = helper if artifact == "helper" else metadata
    target = tmp_path / f"linked-{artifact}"
    target.write_bytes(selected.read_bytes())
    selected.unlink()
    selected.symlink_to(target)
    platform.commands.clear()

    with pytest.raises(SetupError, match="unmanaged native helper"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert selected.is_symlink()


@pytest.mark.parametrize("content", ["not json", "{}", '{"managed_by": "other"}'])
def test_setup_rejects_corrupt_native_metadata(tmp_path, content):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    run_setup(paths, platform=platform)
    helper = paths.application_support / "bin/editor-fcp-bridge"
    helper.with_suffix(".json").write_text(content, encoding="utf-8")
    platform.commands.clear()

    with pytest.raises(SetupError, match="unmanaged native helper"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert helper.read_bytes() == b"reviewed native helper"


def test_setup_repairs_managed_byte_identical_helper_mode(tmp_path):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    run_setup(paths, platform=platform)
    helper = paths.application_support / "bin/editor-fcp-bridge"
    helper.chmod(0o644)
    platform.commands.clear()

    result = run_setup(paths, platform=platform)

    assert helper in result.changed
    assert helper.stat().st_mode & 0o777 == 0o700
    assert platform.commands_contain(["swift", "build"])
    assert platform.commands_contain(["codesign"])


def test_setup_accepts_known_legacy_metadata_and_adds_ownership_marker(tmp_path):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    run_setup(paths, platform=platform)
    helper = paths.application_support / "bin/editor-fcp-bridge"
    metadata_path = helper.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("managed_by", None)
    metadata.pop("metadata_version", None)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    platform.commands.clear()

    run_setup(paths, platform=platform)

    migrated = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert migrated["managed_by"] == "editor-cli.native-final-cut-helper"
    assert migrated["metadata_version"] == 1
    assert platform.commands == []


def test_setup_rejects_unknown_legacy_metadata_state(tmp_path):
    platform = FakePlatform()
    paths = setup_paths(tmp_path)
    run_setup(paths, platform=platform)
    helper = paths.application_support / "bin/editor-fcp-bridge"
    metadata_path = helper.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("managed_by")
    metadata.pop("metadata_version")
    metadata["source_sha256"] = "f" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    platform.commands.clear()

    with pytest.raises(SetupError, match="unmanaged native helper"):
        run_setup(paths, platform=platform)

    assert platform.commands == []


def test_setup_uses_packaged_resources_instead_of_repo_paths(tmp_path):
    paths = setup_paths(tmp_path)
    paths = SetupPaths(
        repo_root=tmp_path / "missing-repository",
        codex_config=paths.codex_config,
        claude_config=paths.claude_config,
        codex_skills=paths.codex_skills,
        claude_skills=paths.claude_skills,
        application_support=paths.application_support,
    )
    platform = FakePlatform()

    run_setup(paths, platform=platform)

    assert platform.commands_contain(["swift", "build", "--package-path"])
    assert (paths.codex_skills / "final-cut-editor").is_symlink()


def test_setup_does_not_request_final_cut_permissions(tmp_path):
    platform = FakePlatform()

    run_setup(setup_paths(tmp_path), platform=platform)

    flattened = "\n".join(" ".join(command) for command in platform.commands)
    assert "permission" not in flattened.lower()
    assert "open " not in flattened.lower()
    assert "osascript" not in flattened.lower()
    assert "websocket" not in flattened.lower()
