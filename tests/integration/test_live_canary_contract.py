import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "fcp_live_canary.py"
_SPEC = importlib.util.spec_from_file_location("fcp_live_canary", _SCRIPT)
assert _SPEC and _SPEC.loader
fcp_live_canary = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = fcp_live_canary
_SPEC.loader.exec_module(fcp_live_canary)


EXPECTED_CHECKS = {
    "source_unchanged",
    "gap_removed",
    "title_visible",
    "transition_visible",
    "reaction_insert_visible",
    "preview_rendered",
    "preview_watched",
}


def test_canary_definition_covers_visible_and_structural_edits():
    expected = json.loads(
        Path("tests/fixtures/canary/expected.json").read_text(encoding="utf-8")
    )
    assert set(expected["required_checks"]) == EXPECTED_CHECKS
    assert expected["duration_seconds"] == 8.0


def test_create_canary_workspace_creates_only_disposable_paths(tmp_path):
    root = tmp_path / "canary"

    workspace = fcp_live_canary.create_canary_workspace(root)

    assert workspace.root == root.resolve()
    assert workspace.source.is_dir()
    assert workspace.sessions.is_dir()
    assert not workspace.library.exists()
    assert workspace.library.parent == workspace.root
    with pytest.raises(FileExistsError):
        fcp_live_canary.create_canary_workspace(root)


def test_create_source_builds_an_eight_second_timeline(monkeypatch, tmp_path):
    workspace = fcp_live_canary.create_canary_workspace(tmp_path / "canary")

    def render(destination, **_kwargs):
        destination.write_bytes(b"generated canary media")

    monkeypatch.setattr(fcp_live_canary, "_render_card", render)
    source = fcp_live_canary.create_source(workspace)

    tree = fcp_live_canary.ET.parse(source)
    assert tree.getroot().find(".//sequence").get("duration") == "8s"
    gaps = tree.getroot().findall(".//gap")
    assert len(gaps) == 1
    assert gaps[0].get("duration") == "1s"
    before = fcp_live_canary.hash_tree(workspace.source)
    (workspace.source / "unexpected-file").write_bytes(b"changed")
    assert fcp_live_canary.hash_tree(workspace.source) != before


def test_canary_program_is_valid_offline_and_covers_required_edits():
    program = fcp_live_canary.canary_program()

    program.validate_for({"duration_seconds": 8.0})
    operations = {(item.group, item.action) for item in program.operations}
    assert operations == {
        ("edit", "fill_gaps"),
        ("edit", "add_transition"),
        ("edit", "add_connected_clip"),
    }
    connected = {
        item.arguments["asset_id"]: item.arguments
        for item in program.operations
        if item.action == "add_connected_clip"
    }
    assert connected["r4"]["offset"] == "1s"
    assert connected["r4"]["duration"] == "2s"
    assert connected["r5"]["offset"] == "5s"
    assert program.changed_ranges == ((1.0, 3.0), (5.0, 6.0))
