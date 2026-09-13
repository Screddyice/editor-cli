import asyncio
import json

import pytest
from typer.testing import CliRunner

from editor_cli.cli import app
from editor_cli.direct.setup import install_skills
from editor_cli.mcp_server import create_mcp


def test_direct_cli_start_accepts_only_explicit_files(tmp_path):
    file = tmp_path / "brief.md"
    file.write_text("The trip context.")
    result = CliRunner().invoke(
        app, ["direct", "start", "--file", str(file), "--prompt", "vlog"]
    )
    assert result.exit_code == 0, result.output
    state = json.loads(result.output)
    assert len(state["assets"]) == 1
    result = CliRunner().invoke(app, ["direct", "run", "status", state["session_dir"]])
    assert result.exit_code == 0
    assert json.loads(result.output)["state"] == "inventory"


def test_mcp_direct_does_not_construct_final_cut_services(monkeypatch):
    def forbidden():
        pytest.fail("Direct editing touched Final Cut services")

    monkeypatch.setattr("editor_cli.mcp_server.build_default_services", forbidden)
    result = asyncio.run(create_mcp().call_tool("editor_direct", {"action": "doctor"}))
    assert result.structured_content["engine"] == "direct"
    assert result.structured_content["final_cut_required"] is False


def test_skill_install_idempotent_and_preserves_unmanaged_content(tmp_path):
    result = install_skills(tmp_path)
    assert len(result["changed"]) == 2
    assert "@PYTHON@" not in open(result["installed"][0]).read()
    assert install_skills(tmp_path)["changed"] == []
    path = tmp_path / ".codex/skills/direct-video-editor/SKILL.md"
    path.write_text("user's unrelated skill")
    with pytest.raises(ValueError, match="unmanaged"):
        install_skills(tmp_path)
    assert path.read_text() == "user's unrelated skill"
