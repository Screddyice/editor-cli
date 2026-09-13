import json
import os
import stat
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


def test_setup_refuses_unmarked_claude_entry_with_installed_command(tmp_path):
    paths = setup_paths(tmp_path)
    python = paths.repo_root / ".venv/bin/python"
    paths.claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "editor-cli": {
                        "type": "stdio",
                        "command": str(python),
                        "args": ["-m", "editor_cli.mcp_server"],
                        "cwd": str(paths.repo_root),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    platform = FakePlatform()

    with pytest.raises(SetupError, match="unmanaged editor-cli"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.application_support.exists()


def test_setup_refuses_marked_claude_entry_with_other_command(tmp_path):
    paths = setup_paths(tmp_path)
    paths.claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "editor-cli": {
                        "managed_by": "editor-cli.mcp-server",
                        "command": "/other/tool",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    platform = FakePlatform()

    with pytest.raises(SetupError, match="unmanaged editor-cli"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.application_support.exists()


def test_setup_refuses_other_claude_marker_with_installed_command(tmp_path):
    paths = setup_paths(tmp_path)
    paths.claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "editor-cli": {
                        "managed_by": "another-installer",
                        "command": str(paths.repo_root / ".venv/bin/python"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    platform = FakePlatform()

    with pytest.raises(SetupError, match="unmanaged editor-cli"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.application_support.exists()


def test_setup_refuses_marked_codex_entry_with_other_command(tmp_path):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    paths.codex_config.write_text(
        "\n".join(
            (
                setup_lib.CODEX_BLOCK_START,
                '[mcp_servers."editor-cli"]',
                'command = "/other/tool"',
                'args = ["-m", "editor_cli.mcp_server"]',
                f"cwd = {json.dumps(str(paths.repo_root))}",
                setup_lib.CODEX_BLOCK_END,
                "",
            )
        ),
        encoding="utf-8",
    )
    platform = FakePlatform()

    with pytest.raises(SetupError, match="unmanaged editor-cli"):
        run_setup(paths, platform=platform)

    assert platform.commands == []
    assert not paths.application_support.exists()


def test_setup_refuses_reversed_codex_ownership_markers(tmp_path):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    paths.codex_config.write_text(
        "\n".join(
            (
                setup_lib.CODEX_BLOCK_END,
                setup_lib.CODEX_BLOCK_START,
                '[mcp_servers."editor-cli"]',
                f"command = {json.dumps(str(paths.repo_root / '.venv/bin/python'))}",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(SetupError, match="ownership marker is malformed"):
        run_setup(paths, platform=FakePlatform())


def test_setup_marks_claude_entry_and_preserves_unrelated_keys(tmp_path):
    paths = setup_paths(tmp_path)
    paths.claude_config.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "other": {"command": "/other/tool", "env": {"SAFE": "1"}}
                },
            }
        ),
        encoding="utf-8",
    )

    run_setup(paths, platform=FakePlatform())

    value = json.loads(paths.claude_config.read_text(encoding="utf-8"))
    assert value["theme"] == "dark"
    assert value["mcpServers"]["other"] == {
        "command": "/other/tool",
        "env": {"SAFE": "1"},
    }
    assert value["mcpServers"]["editor-cli"]["managed_by"] == ("editor-cli.mcp-server")


def test_setup_preserves_unrelated_codex_tables_around_managed_block(tmp_path):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    paths.codex_config.write_text(
        "\n".join(
            (
                'theme = "dark"',
                "",
                "[existing]",
                "value = 1",
                "",
                setup_lib.CODEX_BLOCK_START,
                '[mcp_servers."editor-cli"]',
                f"command = {json.dumps(str(paths.repo_root / '.venv/bin/python'))}",
                'args = ["wrong"]',
                f"cwd = {json.dumps(str(paths.repo_root))}",
                setup_lib.CODEX_BLOCK_END,
                "",
                "[tail]",
                "enabled = true",
                "",
            )
        ),
        encoding="utf-8",
    )

    run_setup(paths, platform=FakePlatform())

    value = setup_lib.tomllib.loads(paths.codex_config.read_text(encoding="utf-8"))
    assert value["theme"] == "dark"
    assert value["existing"] == {"value": 1}
    assert value["tail"] == {"enabled": True}
    assert value["mcp_servers"]["editor-cli"]["args"] == [
        "-m",
        "editor_cli.mcp_server",
    ]


def test_setup_does_not_clobber_config_created_after_preflight(tmp_path):
    paths = setup_paths(tmp_path)
    collision = {"mcpServers": {"editor-cli": {"command": "/created/during/setup"}}}

    class RacingPlatform(FakePlatform):
        def run(self, command, *, cwd=None):
            super().run(command, cwd=cwd)
            if command[:2] == ("swift", "build"):
                paths.claude_config.write_text(json.dumps(collision), encoding="utf-8")

    with pytest.raises(SetupError, match="changed after setup preflight"):
        run_setup(paths, platform=RacingPlatform())

    assert json.loads(paths.claude_config.read_text(encoding="utf-8")) == collision


def test_atomic_update_exchanges_back_existing_config_changed_at_boundary(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    original = b"[existing]\nvalue = 1\n"
    competitor = b"[competitor]\nvalue = 2\n"
    desired = b"[installed]\nvalue = 3\n"
    config.write_bytes(original)
    real_exchange = setup_lib._atomic_exchange
    injected = False

    def exchange_after_change(source, destination):
        nonlocal injected
        if Path(destination) == config and not injected:
            injected = True
            config.write_bytes(competitor)
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", exchange_after_change)

    with pytest.raises(SetupError, match="changed after setup preflight"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            expected=original,
            expected_backup=None,
        )

    assert injected
    assert config.read_bytes() == competitor
    assert config.with_suffix(".toml.editor-cli.bak").read_bytes() == original


def test_atomic_update_restores_symlink_swapped_at_exchange_boundary(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    original = b"[existing]\nvalue = 1\n"
    target = tmp_path / "competitor.toml"
    target.write_bytes(b"[competitor]\nvalue = 2\n")
    config.write_bytes(original)
    real_exchange = setup_lib._atomic_exchange
    injected = False

    def exchange_after_swap(source, destination):
        nonlocal injected
        if Path(destination) == config and not injected:
            injected = True
            config.unlink()
            config.symlink_to(target)
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", exchange_after_swap)

    with pytest.raises(SetupError, match="changed after setup preflight"):
        setup_lib.atomic_config_update(
            config,
            b"[installed]\nvalue = 3\n",
            parse=setup_lib.tomllib.loads,
            expected=original,
            expected_backup=None,
        )

    assert injected
    assert config.is_symlink()
    assert config.resolve() == target.resolve()


def test_atomic_update_does_not_replace_config_created_at_no_replace_boundary(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    competitor = b"[competitor]\nvalue = 2\n"
    desired = b"[installed]\nvalue = 3\n"
    real_move = setup_lib._atomic_move_no_replace
    injected = False

    def move_after_create(source, destination):
        nonlocal injected
        if Path(destination) == config and not injected:
            injected = True
            config.write_bytes(competitor)
        real_move(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_move_no_replace", move_after_create)

    with pytest.raises(SetupError, match="changed after setup preflight"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            expected=None,
            expected_backup=None,
        )

    assert injected
    assert config.read_bytes() == competitor
    assert not config.with_suffix(".toml.editor-cli.bak").exists()


def test_atomic_update_preserves_failed_new_config_and_restores_absence(tmp_path):
    config = tmp_path / "config.toml"
    desired = b"[installed]\nvalue = 3\n"

    def fail_installed_config(path):
        if Path(path) == config:
            raise SetupError("post-write validation failed")

    with pytest.raises(SetupError, match="failed config preserved"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=fail_installed_config,
            expected=None,
            expected_backup=None,
        )

    recoveries = [
        path
        for path in tmp_path.glob(f".{config.name}.*")
        if path.read_bytes() == desired
    ]
    assert not config.exists()
    assert recoveries


def test_atomic_update_exchanges_back_competitor_swapped_during_absent_recovery(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    desired = b"[installed]\nvalue = 3\n"
    competitor = b"[competitor]\nvalue = 4\n"
    real_exchange = setup_lib._atomic_exchange
    injected = False

    def fail_installed_config(path):
        if Path(path) == config:
            raise SetupError("post-write validation failed")

    def exchange_after_swap(source, destination):
        nonlocal injected
        if Path(destination) == config and not injected:
            injected = True
            config.write_bytes(competitor)
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", exchange_after_swap)

    with pytest.raises(SetupError, match="competitor"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=fail_installed_config,
            expected=None,
            expected_backup=None,
        )

    assert injected
    assert config.read_bytes() == competitor


def test_atomic_update_exchanges_back_symlink_swapped_during_absent_recovery(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    target = tmp_path / "competitor.toml"
    desired = b"[installed]\nvalue = 3\n"
    target.write_bytes(b"[competitor]\nvalue = 4\n")
    real_exchange = setup_lib._atomic_exchange
    injected = False

    def fail_installed_config(path):
        if Path(path) == config:
            raise SetupError("post-write validation failed")

    def exchange_after_swap(source, destination):
        nonlocal injected
        if Path(destination) == config and not injected:
            injected = True
            config.unlink()
            config.symlink_to(target)
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", exchange_after_swap)

    with pytest.raises(SetupError, match="competitor"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=fail_installed_config,
            expected=None,
            expected_backup=None,
        )

    assert injected
    assert config.is_symlink()
    assert config.resolve() == target.resolve()


def test_atomic_update_exchanges_back_fifo_swapped_during_absent_recovery(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    desired = b"[installed]\nvalue = 3\n"
    real_exchange = setup_lib._atomic_exchange
    injected = False

    def fail_installed_config(path):
        if Path(path) == config:
            raise SetupError("post-write validation failed")

    def exchange_after_swap(source, destination):
        nonlocal injected
        if Path(destination) == config and not injected:
            injected = True
            config.unlink()
            os.mkfifo(config)
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", exchange_after_swap)

    with pytest.raises(SetupError, match="competitor"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=fail_installed_config,
            expected=None,
            expected_backup=None,
        )

    assert injected
    assert stat.S_ISFIFO(config.lstat().st_mode)


def test_atomic_update_recovers_existing_config_after_install_fsync_failure(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    original = b"[existing]\nvalue = 1\n"
    config.write_bytes(original)
    real_exchange = setup_lib._atomic_exchange
    real_fsync = setup_lib._fsync_directory
    installed = False
    failed = False

    def record_install(source, destination):
        nonlocal installed
        real_exchange(source, destination)
        if Path(destination) == config and not installed:
            installed = True

    def fail_after_install(path):
        nonlocal failed
        if installed and not failed:
            failed = True
            raise OSError("directory fsync failed")
        real_fsync(path)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", record_install)
    monkeypatch.setattr(setup_lib, "_fsync_directory", fail_after_install)

    with pytest.raises(SetupError, match="fsync"):
        setup_lib.atomic_config_update(
            config,
            b"[installed]\nvalue = 3\n",
            parse=setup_lib.tomllib.loads,
            expected=original,
            expected_backup=None,
        )

    assert failed
    assert config.read_bytes() == original
    assert config.with_suffix(".toml.editor-cli.bak").read_bytes() == original


def test_atomic_update_recovers_absence_after_install_fsync_failure(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    desired = b"[installed]\nvalue = 3\n"
    real_move = setup_lib._atomic_move_no_replace
    real_fsync = setup_lib._fsync_directory
    installed = False
    failed = False

    def record_install(source, destination):
        nonlocal installed
        real_move(source, destination)
        if Path(destination) == config:
            installed = True

    def fail_after_install(path):
        nonlocal failed
        if installed and not failed:
            failed = True
            raise OSError("directory fsync failed")
        real_fsync(path)

    monkeypatch.setattr(setup_lib, "_atomic_move_no_replace", record_install)
    monkeypatch.setattr(setup_lib, "_fsync_directory", fail_after_install)

    with pytest.raises(SetupError, match="fsync"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            expected=None,
            expected_backup=None,
        )

    recoveries = [
        path
        for path in tmp_path.glob(f".{config.name}.*")
        if path.read_bytes() == desired
    ]
    assert failed
    assert not config.exists()
    assert recoveries


def test_atomic_update_keeps_valid_competitor_and_displaced_original(
    tmp_path,
):
    config = tmp_path / "config.toml"
    original = b"[existing]\nvalue = 1\n"
    competitor = b"[competitor]\nvalue = 2\n"
    desired = b"[installed]\nvalue = 3\n"
    config.write_bytes(original)
    injected = False

    def replace_during_verification(path):
        nonlocal injected
        if Path(path) == config and not injected:
            injected = True
            config.write_bytes(competitor)

    with pytest.raises(SetupError, match="displaced config preserved"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=replace_during_verification,
            expected=original,
            expected_backup=None,
        )

    recoveries = [
        path
        for path in tmp_path.glob(f".{config.name}.*")
        if path.read_bytes() == original
    ]
    assert injected
    assert config.read_bytes() == competitor
    assert recoveries


def test_atomic_update_revalidates_backup_after_config_verification(
    tmp_path,
):
    config = tmp_path / "config.toml"
    backup = config.with_suffix(".toml.editor-cli.bak")
    original = b"[existing]\nvalue = 1\n"
    competitor = b"[competitor]\nvalue = 2\n"
    desired = b"[installed]\nvalue = 3\n"
    config.write_bytes(original)

    def replace_backup_after_install(path):
        if Path(path) == config:
            backup.write_bytes(competitor)

    with pytest.raises(SetupError, match="backup changed"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=replace_backup_after_install,
            expected=original,
            expected_backup=None,
        )

    assert config.read_bytes() == original
    assert backup.read_bytes() == competitor


def test_atomic_update_revalidates_preexisting_backup_for_absent_config(tmp_path):
    config = tmp_path / "config.toml"
    backup = config.with_suffix(".toml.editor-cli.bak")
    saved = b"[saved]\nvalue = 1\n"
    competitor = b"[competitor]\nvalue = 2\n"
    desired = b"[installed]\nvalue = 3\n"
    backup.write_bytes(saved)

    def replace_backup_after_install(path):
        if Path(path) == config:
            backup.write_bytes(competitor)

    with pytest.raises(SetupError, match="backup changed"):
        setup_lib.atomic_config_update(
            config,
            desired,
            parse=setup_lib.tomllib.loads,
            verify=replace_backup_after_install,
            expected=None,
            expected_backup=saved,
        )

    recoveries = [
        path
        for path in tmp_path.glob(f".{config.name}.*")
        if path.is_file() and not path.is_symlink() and path.read_bytes() == desired
    ]
    assert not config.exists()
    assert backup.read_bytes() == competitor
    assert recoveries


def test_snapshot_regular_file_rejects_symlink_swapped_before_open(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    target = tmp_path / "competitor.toml"
    config.write_bytes(b"[existing]\nvalue = 1\n")
    target.write_bytes(b"[competitor]\nvalue = 2\n")
    real_open = setup_lib.os.open
    injected = False

    def swap_before_open(path, flags, *args):
        nonlocal injected
        if Path(path) == config and not injected:
            injected = True
            config.unlink()
            config.symlink_to(target)
        return real_open(path, flags, *args)

    monkeypatch.setattr(setup_lib.os, "open", swap_before_open)

    with pytest.raises(SetupError, match="changed while setup read"):
        setup_lib._snapshot_regular_file(config, label="Config")

    assert injected
    assert config.is_symlink()
    assert config.resolve() == target.resolve()


def test_atomic_update_refuses_partial_backup_before_config_exchange(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    original = b"[existing]\nvalue = 1\n"
    backup = config.with_suffix(".toml.editor-cli.bak")
    config.write_bytes(original)
    backup.write_bytes(b"not toml")
    exchanges = 0
    real_exchange = setup_lib._atomic_exchange

    def record_exchange(source, destination):
        nonlocal exchanges
        exchanges += 1
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_atomic_exchange", record_exchange)

    with pytest.raises(SetupError, match="backup is invalid"):
        setup_lib.atomic_config_update(
            config,
            b"[installed]\nvalue = 3\n",
            parse=setup_lib.tomllib.loads,
            expected=original,
            expected_backup=b"not toml",
        )

    assert exchanges == 0
    assert config.read_bytes() == original
    assert backup.read_bytes() == b"not toml"


def test_atomic_update_rechecks_new_backup_after_no_replace_publication(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.toml"
    backup = config.with_suffix(".toml.editor-cli.bak")
    original = b"[existing]\nvalue = 1\n"
    competitor = b"[competitor]\nvalue = 2\n"
    config.write_bytes(original)
    real_move = setup_lib._atomic_move_no_replace
    injected = False

    def move_then_replace(source, destination):
        nonlocal injected
        real_move(source, destination)
        if Path(destination) == backup and not injected:
            injected = True
            backup.write_bytes(competitor)

    monkeypatch.setattr(setup_lib, "_atomic_move_no_replace", move_then_replace)

    with pytest.raises(SetupError, match="backup changed"):
        setup_lib.atomic_config_update(
            config,
            b"[installed]\nvalue = 3\n",
            parse=setup_lib.tomllib.loads,
            expected=original,
            expected_backup=None,
        )

    assert injected
    assert config.read_bytes() == original
    assert backup.read_bytes() == competitor


def test_atomic_config_update_validates_temp_before_replacement(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    original = b"[existing]\nvalue = 1\n"
    config.write_bytes(original)
    replacements = []
    real_replace = setup_lib.os.replace

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(setup_lib.os, "replace", record_replace)

    with pytest.raises(SetupError, match="invalid configuration"):
        setup_lib.atomic_config_update(
            config, b"not toml", parse=setup_lib.tomllib.loads
        )

    assert config.read_bytes() == original
    assert not config.with_suffix(".toml.editor-cli.bak").exists()
    assert all(destination != config for _, destination in replacements)
    assert list(tmp_path.glob(f".{config.name}.*")) == []


def test_setup_restores_fixed_backup_after_later_verification_failure(
    tmp_path, monkeypatch
):
    paths = setup_paths(tmp_path)
    paths.codex_config.parent.mkdir(parents=True)
    original = "[existing]\nvalue = 1\n"
    paths.codex_config.write_text(original, encoding="utf-8")
    platform = FakePlatform()
    run_setup(paths, platform=platform)
    backup = paths.codex_config.with_suffix(".toml.editor-cli.bak")
    assert backup.read_text(encoding="utf-8") == original

    configured = paths.codex_config.read_text(encoding="utf-8")
    paths.codex_config.write_text(
        configured.replace(
            'args = ["-m", "editor_cli.mcp_server"]', 'args = ["wrong"]'
        ),
        encoding="utf-8",
    )
    real_validate = setup_lib._validate_config_file
    failed = False
    replacements = []
    real_exchange = setup_lib._atomic_exchange

    def fail_once(path):
        nonlocal failed
        if path == paths.codex_config and not failed:
            failed = True
            raise SetupError("post-write parse failed")
        return real_validate(path)

    def record_exchange(source, destination):
        if Path(destination) == paths.codex_config:
            replacements.append(Path(source))
        real_exchange(source, destination)

    monkeypatch.setattr(setup_lib, "_validate_config_file", fail_once)
    monkeypatch.setattr(setup_lib, "_atomic_exchange", record_exchange)

    with pytest.raises(SetupError, match="post-write parse failed") as error:
        run_setup(paths, platform=platform)

    assert paths.codex_config.read_text(encoding="utf-8") == original
    assert len(replacements) == 2
    assert all(source.parent == paths.codex_config.parent for source in replacements)
    assert "displaced config preserved at" in str(error.value)


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
