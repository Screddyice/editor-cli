"""Crossfade / wipe transitions — segments overlap via ffmpeg xfade + acrossfade.

These need a single filter_complex graph (segments share time), so they take a
different render path than hard cuts. An edit with no crossfade still uses the
proven per-segment-encode + concat-copy path, untouched.
"""

import json
import subprocess

import pytest

from editor_cli.domain.edl import EDL, Segment
from editor_cli.render import ffmpeg
from editor_cli.render.ffmpeg import render_edl


def _make_clip(path, size="640x360", seconds=3):
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _dims(path):
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return int(v["width"]), int(v["height"])


def _record(monkeypatch):
    calls = []

    def fake_run(cmd):
        calls.append(cmd)

        class R:
            stdout = json.dumps(
                {"streams": [{"codec_type": "video", "width": 640, "height": 360}],
                 "format": {"duration": "3.0"}}
            )
        return R()

    monkeypatch.setattr(ffmpeg, "_run", fake_run)
    return calls


# --- domain ------------------------------------------------------------------

def test_crossfade_roundtrips():
    seg = Segment(src="a.mp4", in_=0.0, out=2.0,
                  transition={"crossfade": 0.5, "crossfade_style": "wipeleft"})
    back = Segment.from_dict(json.loads(json.dumps(seg.to_dict())))
    assert back.transition == seg.transition


# --- dispatch / filtergraph (mocked) -----------------------------------------

def test_no_crossfade_uses_concat_path(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src="a.mp4", in_=0.0, out=1.0),
        Segment(src="a.mp4", in_=1.0, out=2.0)])
    render_edl(edl, "o.mp4")
    assert not any("-filter_complex" in c for c in calls)


def test_crossfade_uses_filter_complex_with_xfade_and_acrossfade(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src="a.mp4", in_=0.0, out=1.0),
        Segment(src="a.mp4", in_=1.0, out=2.0, transition={"crossfade": 0.5})])
    render_edl(edl, "o.mp4")
    fc = next(c for c in calls if "-filter_complex" in c)
    graph = fc[fc.index("-filter_complex") + 1]
    assert "xfade=transition=fade" in graph
    assert "acrossfade=d=0.5" in graph


def test_crossfade_style_passthrough(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src="a.mp4", in_=0.0, out=1.0),
        Segment(src="a.mp4", in_=1.0, out=2.0,
                transition={"crossfade": 0.4, "crossfade_style": "wipeleft"})])
    render_edl(edl, "o.mp4")
    fc = next(c for c in calls if "-filter_complex" in c)
    assert "xfade=transition=wipeleft" in fc[fc.index("-filter_complex") + 1]


# --- real renders ------------------------------------------------------------

def test_crossfade_shortens_total_by_overlap(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    # three 1.0s segments, 0.5s crossfade into 2nd and 3rd -> 3.0 - 1.0 = 2.0s
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src=str(clip), in_=0.0, out=1.0),
        Segment(src=str(clip), in_=1.0, out=2.0, transition={"crossfade": 0.5}),
        Segment(src=str(clip), in_=2.0, out=3.0, transition={"crossfade": 0.5})])
    out = tmp_path / "o.mp4"
    render_edl(edl, str(out))
    assert _dims(out) == (640, 360)
    assert ffmpeg.duration_of(str(out)) == pytest.approx(2.0, abs=0.3)


def test_mixed_crossfade_and_hard_cut(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    # seg1 crossfades (0.5), seg2 hard cut -> 3.0 - 0.5 = 2.5s
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src=str(clip), in_=0.0, out=1.0),
        Segment(src=str(clip), in_=1.0, out=2.0, transition={"crossfade": 0.5}),
        Segment(src=str(clip), in_=2.0, out=3.0)])
    out = tmp_path / "o.mp4"
    render_edl(edl, str(out))
    assert ffmpeg.duration_of(str(out)) == pytest.approx(2.5, abs=0.3)


def test_overlong_crossfade_is_clamped_and_renders(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    # crossfade longer than the clips must clamp, not produce a negative offset
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src=str(clip), in_=0.0, out=1.0),
        Segment(src=str(clip), in_=1.0, out=2.0, transition={"crossfade": 5.0})])
    out = tmp_path / "o.mp4"
    render_edl(edl, str(out))
    assert out.exists()
    assert ffmpeg.duration_of(str(out)) > 0.5
