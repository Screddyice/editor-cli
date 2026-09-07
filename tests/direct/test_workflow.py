import json
import shutil
import subprocess
from pathlib import Path

import pytest

from editor_cli.direct.media import digest
from editor_cli.direct.session import DirectSession, atomic_json, speech_edges


def approval(attempt, passed=True):
    return {"render_id": attempt["id"], "sha256": attempt["sha256"],
            "windows": [{"id": w["id"], "visual": "Synthetic test fixture",
                         "audio": "Synthetic silent fixture", "passed": passed}
                        for w in attempt["windows"]], "summary": "Synthetic contract test."}


@pytest.fixture
def session(tmp_path, monkeypatch):
    from tests.direct.test_session import start
    session, state = start(tmp_path, monkeypatch)
    monkeypatch.setattr("editor_cli.direct.session.run", lambda *a, **k: None)
    monkeypatch.setattr("editor_cli.direct.session.probe", lambda p: {
        "format": {"duration": "2"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]})
    def render(plan, assets, transcripts, work, preview=False):
        path = work / "render.mp4"
        path.write_bytes(b"render" + work.name.encode())
        return path
    monkeypatch.setattr("editor_cli.direct.render.render", render)
    monkeypatch.setattr(session, "review_windows", lambda *a: [{"id": "window_0", "sha256": {}}])
    session.approve("Keep the strongest moment and finish.")
    plan = {"cuts": [{"asset_id": next(iter(state["assets"])), "start": 0,
                     "end": 2, "kind": "broll", "reason": "strong opening"}]}
    return session, plan


def test_requires_review_of_final_render(session):
    session, plan = session
    first = session.render(plan)
    with pytest.raises(ValueError, match="passing review"):
        session.render(final=True)
    session.review(approval(first))
    final = session.render(final=True)
    with pytest.raises(ValueError, match="latest render"):
        session.review(approval(first))
    with pytest.raises(ValueError, match="passing review"):
        session.finish()
    session.review(approval(final))
    result = session.finish()
    assert digest(Path(result["video"])) == final["sha256"]
    assert DirectSession(session.root).finish() == result


def test_new_unreviewed_preview_blocks_export_even_same_plan(session):
    session, plan = session
    session.review(approval(session.render(plan)))
    session.render(plan)
    with pytest.raises(ValueError, match="passing review"):
        session.render(final=True)


def test_reviews_require_all_windows_and_cannot_reuse_modified_video(session):
    session, plan = session
    attempt = session.render(plan)
    report = approval(attempt)
    report["windows"] = []
    with pytest.raises(ValueError, match="every required window"):
        session.review(report)
    Path(attempt["path"]).write_bytes(b"different")
    with pytest.raises(ValueError, match="changed"):
        session.review(approval(attempt))


def test_three_preview_limit_survives_new_session_object(session):
    session, plan = session
    for _ in range(3):
        session.review(approval(session.render(plan), False))
    with pytest.raises(ValueError, match="Three preview"):
        DirectSession(session.root).render(plan)


def test_failed_render_can_retry_without_spending_preview_pass(session, monkeypatch):
    session, plan = session
    original = __import__("editor_cli.direct.render", fromlist=["render"]).render
    def fail(*a, **k):
        raise RuntimeError("simulated renderer crash")
    monkeypatch.setattr("editor_cli.direct.render.render", fail)
    with pytest.raises(RuntimeError):
        session.render(plan)
    monkeypatch.setattr("editor_cli.direct.render.render", original)
    result = session.render(plan)
    assert result["status"] == "rendered"
    state = session.status()
    assert [a["status"] for a in state["attempts"]] == ["failed", "rendered"]


def test_word_edges_and_padding():
    transcript = {"words": [{"text": "Hello", "start": 0.5, "end": 1.0},
                            {"text": "world", "start": 1.5, "end": 2.0}]}
    for a, b in [(0.7, 2.1), (0.5, 2.1), (0.4, 2.0)]:
        with pytest.raises(ValueError):
            speech_edges({"start": a, "end": b}, transcript, 3)
    speech_edges({"start": 0.4, "end": 2.1}, transcript, 3)


def test_interrupted_final_copy_is_retryable(session, monkeypatch):
    session, plan = session
    session.review(approval(session.render(plan)))
    session.review(approval(session.render(final=True)))
    original = shutil.copyfileobj
    def interrupt(source, dest, *args, **kwargs):
        dest.write(b"partial")
        raise OSError("disk temporarily full")
    monkeypatch.setattr(shutil, "copyfileobj", interrupt)
    with pytest.raises(OSError):
        session.finish()
    assert not (session.root / "final.mp4").exists()
    monkeypatch.setattr(shutil, "copyfileobj", original)
    assert DirectSession(session.root).finish()["state"] == "finished"


def test_resume_checks_accepted_render_and_delivered_file(session):
    session, plan = session
    preview = session.render(plan)
    session.review(approval(preview))
    rendered = Path(preview["path"])
    saved = rendered.read_bytes()
    rendered.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        DirectSession(session.root).status()
    rendered.write_bytes(saved)
    session.review(approval(session.render(final=True)))
    result = session.finish()
    Path(result["video"]).write_bytes(b"changed delivery")
    with pytest.raises(ValueError, match="Delivered final video changed"):
        DirectSession(session.root).status()


def test_no_audio_transcript_cached_by_asset_and_hash(session):
    session, plan = session
    asset_id = plan["cuts"][0]["asset_id"]
    assert session.transcribe(asset_id)["transcript"] == {"text": "", "words": []}
    assert session.transcribe(asset_id)["cached"] is True
    record = session.status()["transcripts"][asset_id]
    Path(record["path"]).write_text('{"words":[]}')
    with pytest.raises(ValueError, match="transcript changed"):
        session.transcribe(asset_id)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_real_session_render_review_export_finish(tmp_path):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=blue:s=320x180:r=30", "-t", "1.5", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(video)], check=True)
    source_hash = digest(video)
    inventory = DirectSession.start([str(video)], "synthetic two-cut acceptance")
    session = DirectSession(Path(inventory["session_dir"]))
    asset_id = next(iter(inventory["assets"]))
    session.approve("Synthetic fixture: two blue shots with a title.")
    plan = {"width": 320, "height": 180, "cuts": [
        {"asset_id": asset_id, "start": 0, "end": 0.6, "kind": "broll", "reason": "opening"},
        {"asset_id": asset_id, "start": 0.7, "end": 1.4, "kind": "broll", "reason": "ending"}],
        "titles": [{"text": "DIRECT EDIT", "start": 0, "duration": 1}]}
    preview = session.render(plan)
    assert all(Path(w["images"][0]).exists() and Path(w["audio"]).exists()
               for w in preview["windows"])
    session.review(approval(preview))
    final = session.render(final=True)
    session.review(approval(final))
    result = DirectSession(session.root).finish()
    assert digest(video) == source_hash
    assert Path(result["video"]).is_file()
    assert json.loads((session.root / "edl.json").read_text())["width"] == 320
