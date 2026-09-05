from pathlib import Path

import pytest

import editor_cli.setup as setup_lib
from editor_cli.setup import SetupError, SetupPaths, run_setup


def test_watch_install_command_pins_shared_skill_release():
    assert setup_lib.watch_install_command() == (
        "npx",
        "skills",
        "add",
        "https://github.com/bradautomates/claude-video/tree/v0.2.0",
        "-g",
        "--agent",
        "claude-code",
        "codex",
        "--skill",
        "watch",
        "-y",
    )


class FakePlatform:
    def __init__(self):
        self.commands = []
        self.mcp_checks = 0
        self.python = None

    def run(self, command, *, cwd=None):
        self.commands.append(tuple(command))
        if command[:2] == ("swift", "build"):
            source = Path(command[command.index("--package-path") + 1])
            binary = source / ".build/release/FinalCutBridge"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"native helper")

    def install_watch(self, paths):
        for root in (paths.codex_skills, paths.claude_skills):
            skill = root / "watch"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(
                "---\nname: watch\nversion: 0.2.0\n---\n",
                encoding="utf-8",
            )

    def verify_mcp(self, python, _repo_root):
        self.mcp_checks += 1
        self.python = python
        return True

    def helper_has_signature(self, _path, _identifier):
        return True


def setup_paths(tmp_path: Path) -> SetupPaths:
    return SetupPaths(
        repo_root=Path.cwd(),
        codex_config=tmp_path / "codex" / "config.toml",
        claude_config=tmp_path / "claude.json",
        codex_skills=tmp_path / "codex" / "skills",
        claude_skills=tmp_path / "claude" / "skills",
        application_support=tmp_path / "Library/Application Support/Editor CLI",
    )


def test_setup_backs_up_changed_agent_config(tmp_path):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    paths.codex_config.write_text("[existing]\nvalue = 1\n")
    result = run_setup(paths, platform=FakePlatform())
    assert result.backups == [paths.codex_config.with_suffix(".toml.editor-cli.bak")]
    assert "[existing]" in paths.codex_config.read_text()
    assert '[mcp_servers."editor-cli"]' in paths.codex_config.read_text()


def test_setup_second_run_has_no_changes(tmp_path):
    paths = setup_paths(tmp_path)
    platform = FakePlatform()
    first = run_setup(paths, platform=platform)
    second = run_setup(paths, platform=platform)
    assert first.changed
    assert second.changed == []


def test_setup_dry_run_does_not_write_outside_repo(tmp_path):
    paths = setup_paths(tmp_path)
    result = run_setup(paths, platform=FakePlatform(), dry_run=True)
    assert result.planned
    assert not paths.codex_config.exists()
    assert not paths.claude_config.exists()
    assert not paths.application_support.exists()


def test_setup_keeps_virtualenv_python_path(tmp_path):
    paths = setup_paths(tmp_path)
    platform = FakePlatform()
    run_setup(paths, platform=platform)
    assert platform.python == paths.repo_root / ".venv/bin/python"


def test_setup_refuses_unmanaged_claude_editor_cli_entry(tmp_path):
    paths = setup_paths(tmp_path)
    paths.claude_config.write_text(
        '{"mcpServers":{"editor-cli":{"command":"someone-else"}}}\n',
        encoding="utf-8",
    )

    with pytest.raises(SetupError, match="unmanaged editor-cli"):
        run_setup(paths, platform=FakePlatform())

    assert "someone-else" in paths.claude_config.read_text(encoding="utf-8")


def test_setup_rolls_back_config_when_post_write_validation_fails(
    tmp_path, monkeypatch
):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    original = "[existing]\nvalue = 1\n"
    paths.codex_config.write_text(original, encoding="utf-8")
    real_validate = setup_lib._validate_config_file
    failed = False

    def fail_once(path):
        nonlocal failed
        if path == paths.codex_config and not failed:
            failed = True
            raise SetupError("post-write parse failed")
        return real_validate(path)

    monkeypatch.setattr(setup_lib, "_validate_config_file", fail_once)

    with pytest.raises(SetupError, match="post-write parse failed"):
        run_setup(paths, platform=FakePlatform())

    assert paths.codex_config.read_text(encoding="utf-8") == original


def test_setup_writes_native_helper_metadata_without_websocket_configuration(tmp_path):
    paths = setup_paths(tmp_path)

    run_setup(paths, platform=FakePlatform())

    metadata = paths.application_support / "bin/editor-fcp-bridge.json"
    value = setup_lib.json.loads(metadata.read_text(encoding="utf-8"))
    assert value["protocol_version"] == 1
    assert len(value["sha256"]) == 64
    assert "port" not in value
    assert "websocket" not in metadata.read_text(encoding="utf-8").lower()


def test_setup_migrates_exact_legacy_final_cut_skill_links(tmp_path):
    paths = setup_paths(tmp_path)
    legacy = paths.repo_root / "skills/final-cut-editor"
    for root in (paths.codex_skills, paths.claude_skills):
        root.mkdir(parents=True)
        (root / "final-cut-editor").symlink_to(legacy, target_is_directory=True)

    result = run_setup(paths, platform=FakePlatform())

    packaged = setup_lib._resource_path(setup_lib.final_cut_skill())
    for root in (paths.codex_skills, paths.claude_skills):
        assert (root / "final-cut-editor").resolve() == packaged
    assert sum(str(item).startswith("migrate ") for item in result.changed) == 2
    assert len(result.backups) == 2
    for backup in result.backups:
        assert backup.is_symlink()
        assert backup.resolve() == legacy.resolve()


def test_setup_preflights_arbitrary_skill_collision_before_native_build(tmp_path):
    paths = setup_paths(tmp_path)
    arbitrary = tmp_path / "arbitrary-skill"
    arbitrary.mkdir()
    paths.claude_skills.mkdir(parents=True)
    link = paths.claude_skills / "final-cut-editor"
    link.symlink_to(arbitrary, target_is_directory=True)
    platform = FakePlatform()

    with pytest.raises(SetupError, match="Refusing to replace existing path"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.application_support.exists()
    assert link.resolve() == arbitrary.resolve()


def test_setup_preflights_config_collision_before_native_build(tmp_path):
    paths = setup_paths(tmp_path)
    paths.claude_config.write_text(
        '{"mcpServers":{"editor-cli":{"command":"someone-else"}}}\n',
        encoding="utf-8",
    )
    platform = FakePlatform()

    with pytest.raises(SetupError, match="unmanaged editor-cli"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.application_support.exists()


def test_setup_does_not_clobber_skill_created_after_preflight(tmp_path):
    paths = setup_paths(tmp_path)
    collision = paths.codex_skills / "final-cut-editor"

    class RacingPlatform(FakePlatform):
        def install_watch(self, setup_paths):
            super().install_watch(setup_paths)
            collision.write_text("created by another process", encoding="utf-8")

    with pytest.raises(SetupError, match="changed after setup preflight"):
        run_setup(paths, platform=RacingPlatform())

    assert collision.read_text(encoding="utf-8") == "created by another process"
    assert not collision.is_symlink()


def test_setup_does_not_clobber_legacy_skill_changed_after_preflight(tmp_path):
    paths = setup_paths(tmp_path)
    legacy = paths.repo_root / "skills/final-cut-editor"
    link = paths.codex_skills / "final-cut-editor"
    link.parent.mkdir(parents=True)
    link.symlink_to(legacy, target_is_directory=True)
    replacement = tmp_path / "replacement-skill"
    replacement.mkdir()

    class RacingPlatform(FakePlatform):
        def install_watch(self, setup_paths):
            super().install_watch(setup_paths)
            link.unlink()
            link.symlink_to(replacement, target_is_directory=True)

    with pytest.raises(SetupError, match="changed after setup preflight"):
        run_setup(paths, platform=RacingPlatform())

    assert link.is_symlink()
    assert link.resolve() == replacement.resolve()


def test_setup_leaves_legacy_skill_usable_without_atomic_exchange(
    tmp_path, monkeypatch
):
    paths = setup_paths(tmp_path)
    legacy = paths.repo_root / "skills/final-cut-editor"
    link = paths.codex_skills / "final-cut-editor"
    link.parent.mkdir(parents=True)
    link.symlink_to(legacy, target_is_directory=True)
    monkeypatch.setattr(setup_lib.sys, "platform", "unsupported")

    with pytest.raises(SetupError, match="legacy link was left unchanged"):
        run_setup(paths, platform=FakePlatform())

    assert link.is_symlink()
    assert link.resolve() == legacy.resolve()


def test_setup_restores_skill_swapped_immediately_before_legacy_install(
    tmp_path, monkeypatch
):
    paths = setup_paths(tmp_path)
    legacy = paths.repo_root / "skills/final-cut-editor"
    link = paths.codex_skills / "final-cut-editor"
    link.parent.mkdir(parents=True)
    link.symlink_to(legacy, target_is_directory=True)
    replacement = tmp_path / "replacement-skill"
    replacement.mkdir()
    real_exchange = setup_lib._atomic_exchange
    injected = False

    def exchange_after_swap(source, destination):
        nonlocal injected
        if destination == link and not injected:
            injected = True
            link.unlink()
            link.symlink_to(replacement, target_is_directory=True)
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", exchange_after_swap)

    with pytest.raises(SetupError, match="changed after setup preflight") as error:
        run_setup(paths, platform=FakePlatform())

    assert injected
    assert link.is_symlink()
    assert link.resolve() == replacement.resolve()
    assert replacement.is_dir()
    assert "staged replacement is preserved at" in str(error.value)
