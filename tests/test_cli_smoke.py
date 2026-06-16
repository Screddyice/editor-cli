from typer.testing import CliRunner

from editor1.cli import app


def test_help_lists_edit_command():
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "edit" in res.output


def test_help_lists_style_command():
    res = CliRunner().invoke(app, ["--help"])
    assert "style" in res.output
