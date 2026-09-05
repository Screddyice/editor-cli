import subprocess
from pathlib import Path
from unittest.mock import Mock

from typer.testing import CliRunner

from editor_cli.cli import app
from editor_cli.config import ControllerConfig


def test_help_lists_edit_command():
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "edit" in res.output


def test_help_lists_style_command():
    res = CliRunner().invoke(app, ["--help"])
    assert "style" in res.output


def test_help_lists_controller_setup_doctor_and_permissions_commands():
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "setup" in res.output
    assert "doctor" in res.output
    assert "permissions" in res.output


def test_setup_dry_run_reports_planned_changes(monkeypatch):
    from editor_cli.setup import SetupResult

    monkeypatch.setattr(
        "editor_cli.setup.run_setup",
        lambda **_kwargs: SetupResult(planned=["build and install native helper"]),
    )
    res = CliRunner().invoke(app, ["setup", "--dry-run"])
    assert res.exit_code == 0
    assert "build and install native helper" in res.output


def test_doctor_reports_final_cut_version(monkeypatch):
    monkeypatch.setattr(
        "editor_cli.mcp_server.device_report",
        lambda: {
            "native_helper": {
                "installed": True,
                "metadata_valid": True,
                "protocol_version": 1,
                "compatible": True,
            },
            "final_cut": {
                "bundle_id": "com.apple.FinalCutApp",
                "version": "12.3",
                "compatible": True,
            },
            "permissions": {"accessibility": True, "automation": True},
            "dialogs": [],
            "dialogs_checked": True,
            "ready": True,
        },
    )
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0
    assert "Final Cut Pro 12.3" in res.output
    assert "✓ Native helper protocol 1" in res.output
    assert "Accessibility" in res.output
    assert "Automation" in res.output
    assert "CommandPost" not in res.output


def test_doctor_does_not_report_clear_dialogs_when_probe_did_not_run(monkeypatch):
    monkeypatch.setattr(
        "editor_cli.mcp_server.device_report",
        lambda: {
            "native_helper": {
                "installed": False,
                "metadata_valid": False,
                "protocol_version": None,
                "compatible": False,
            },
            "final_cut": {"bundle_id": None, "version": None, "compatible": False},
            "permissions": {"accessibility": False, "automation": False},
            "dialogs": None,
            "dialogs_checked": False,
            "ready": False,
            "error": "Native helper metadata is invalid",
        },
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "No blocking dialogs" not in result.output
    assert "Blocking dialog inspection unavailable" in result.output


def test_permission_request_invokes_only_installed_helper_argv_mode(
    monkeypatch, tmp_path
):
    helper = tmp_path / "editor-fcp-bridge"
    helper.write_bytes(b"helper")
    config = ControllerConfig(session_root=tmp_path / "sessions", native_helper=helper)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, "Permissions requested.\n", "")

    monkeypatch.setattr("editor_cli.config.load_controller_config", lambda: config)
    monkeypatch.setattr("editor_cli.cli.subprocess.run", run)

    result = CliRunner().invoke(app, ["permissions", "request"])

    assert result.exit_code == 0
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][1:] == ["--request-permissions"]
    assert Path(args[0][0]).name == helper.name
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 120
    assert len(kwargs["pass_fds"]) == 1
    assert "input" not in kwargs
    assert "Permissions requested." in result.output


def test_permission_request_rejects_symlinked_helper_before_execution(
    monkeypatch, tmp_path
):
    target = tmp_path / "target-helper"
    target.write_bytes(b"helper")
    helper = tmp_path / "editor-fcp-bridge"
    helper.symlink_to(target)
    config = ControllerConfig(session_root=tmp_path / "sessions", native_helper=helper)
    run = Mock()
    monkeypatch.setattr("editor_cli.config.load_controller_config", lambda: config)
    monkeypatch.setattr("editor_cli.cli.subprocess.run", run)

    result = CliRunner().invoke(app, ["permissions", "request"])

    assert result.exit_code == 1
    assert "regular file" in result.output
    run.assert_not_called()
