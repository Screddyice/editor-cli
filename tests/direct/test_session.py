import json
from pathlib import Path

import pytest

from editor_cli.direct.session import DirectSession


def start(tmp_path, monkeypatch):
    source = tmp_path / "selected.mp4"
    source.write_bytes(b"selected content")
    (tmp_path / "private.mp4").write_bytes(b"not selected")
    monkeypatch.setattr("editor_cli.direct.session.probe", lambda p: {
        "format": {"duration": "5"},
        "streams": [{"codec_type": "video", "width": 320, "height": 180}],
    })
    result = DirectSession.start([str(source)], "make a vlog")
    return DirectSession(Path(result["session_dir"])), result


def test_only_selected_files_are_snapshotted(tmp_path, monkeypatch):
    session, result = start(tmp_path, monkeypatch)
    assert len(result["assets"]) == 1
    asset = next(iter(result["assets"].values()))
    assert Path(asset["path"]).read_bytes() == b"selected content"
    assert "private.mp4" not in json.dumps(result)
    assert session.status()["state"] == "inventory"


def test_rejects_directory_symlink_and_playlist(tmp_path):
    file = tmp_path / "secret.mp4"
    file.write_bytes(b"x")
    link = tmp_path / "alias.mp4"
    link.symlink_to(file)
    for path in (tmp_path, link, tmp_path / "foo.m3u8"):
        with pytest.raises((ValueError, OSError)):
            DirectSession.start([str(path)], "vlog")


def test_requires_strategy_and_rejects_unknown_asset(tmp_path, monkeypatch):
    session, result = start(tmp_path, monkeypatch)
    asset_id = next(iter(result["assets"]))
    plan = {"cuts": [{"asset_id": asset_id, "start": 0, "end": 2,
                     "kind": "broll", "reason": "opening"}]}
    with pytest.raises(ValueError, match="strategy"):
        session.render(plan)
    session.approve("Short chronological vlog; preserve the arrival.")
    plan["cuts"][0]["asset_id"] = "/private/other.mp4"
    with pytest.raises(ValueError, match="asset"):
        session.render(plan)


def test_source_mutation_blocks_resume(tmp_path, monkeypatch):
    session, _ = start(tmp_path, monkeypatch)
    (tmp_path / "selected.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        session.status()


def test_context_selection_reads_only_exact_document(tmp_path):
    source = tmp_path / "brief.md"
    source.write_text("A weekend in Da Nang. Keep the beach sequence.")
    result = DirectSession.start([str(source)], "read this brief")
    assert next(iter(result["assets"].values()))["context"].startswith("A weekend")


def test_nonfinite_plan_rejected():
    from editor_cli.direct.models import EditPlan
    with pytest.raises(ValueError):
        EditPlan.model_validate({"cuts": [{"asset_id": "a", "start": 0,
            "end": float("inf"), "reason": "invalid"}]})
