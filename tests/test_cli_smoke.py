from typer.testing import CliRunner

from editor_cli.cli import app


def test_help_lists_edit_command():
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "edit" in res.output


def test_help_lists_style_command():
    res = CliRunner().invoke(app, ["--help"])
    assert "style" in res.output


def test_help_lists_controller_setup_and_doctor_commands():
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "setup" in res.output
    assert "doctor" in res.output


def test_setup_dry_run_reports_planned_changes(monkeypatch):
    from editor_cli.setup import SetupResult

    monkeypatch.setattr(
        "editor_cli.setup.run_setup",
        lambda **_kwargs: SetupResult(planned=["install CommandPost 2.1.0"]),
    )
    res = CliRunner().invoke(app, ["setup", "--dry-run"])
    assert res.exit_code == 0
    assert "install CommandPost 2.1.0" in res.output


def test_doctor_reports_final_cut_version(monkeypatch):
    monkeypatch.setattr(
        "editor_cli.mcp_server.device_report",
        lambda: {
            "final_cut": {"installed": True, "version": "12.3", "build": "450152"},
            "commandpost": {"installed": True},
            "watch": {"codex": True, "claude_code": True},
        },
    )
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0
    assert "Final Cut Pro 12.3" in res.output
