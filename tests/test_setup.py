from pathlib import Path

from editor_cli.setup import SetupPaths, run_setup, watch_install_command


def test_watch_install_command_pins_shared_skill_release():
    assert watch_install_command() == (
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
        self.version = "2.1.0"
        self.installs = 0
        self.mcp_checks = 0
        self.python = None

    def commandpost_version(self, _applications):
        return self.version

    def install_commandpost(self, _applications):
        self.installs += 1
        self.version = "2.1.0"

    def install_watch(self, paths):
        for root in (paths.codex_skills, paths.claude_skills):
            skill = root / "watch"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text("---\nname: watch\nversion: 0.2.0\n---\n")

    def verify_mcp(self, python, _repo_root):
        self.mcp_checks += 1
        self.python = python
        return True


def setup_paths(tmp_path: Path) -> SetupPaths:
    return SetupPaths(
        repo_root=Path.cwd(),
        codex_config=tmp_path / "codex" / "config.toml",
        claude_config=tmp_path / "claude.json",
        codex_skills=tmp_path / "codex" / "skills",
        claude_skills=tmp_path / "claude" / "skills",
        commandpost_plugins=tmp_path / "CommandPost" / "Plugins",
        applications=tmp_path / "Applications",
    )


def test_setup_backs_up_changed_agent_config(tmp_path):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    paths.codex_config.write_text("[existing]\nvalue = 1\n")
    result = run_setup(paths, platform=FakePlatform())
    assert result.backups == [
        paths.codex_config.with_suffix(".toml.editor-cli.bak")
    ]
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
    assert not paths.commandpost_plugins.exists()


def test_setup_keeps_virtualenv_python_path(tmp_path):
    paths = setup_paths(tmp_path)
    platform = FakePlatform()
    run_setup(paths, platform=platform)
    assert platform.python == paths.repo_root / ".venv/bin/python"
