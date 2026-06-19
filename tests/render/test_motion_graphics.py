"""Native motion-graphics primitives — Ken Burns, speed ramp, fade transitions.

All are per-segment and opt-in: a Segment with no motion/transition renders
exactly as before. Built on ffmpeg only (no AGPL OpenMontage code) — its ffmpeg
skill was reference for the filter techniques.
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


# --- domain round-trip -------------------------------------------------------

def test_segment_roundtrips_motion_and_transition():
    seg = Segment(
        src="a.mp4", in_=0.0, out=2.0,
        motion={"type": "ken_burns", "zoom": 1.12, "direction": "in"},
        transition={"fade_in": 0.5, "fade_out": 0.4},
    )
    back = Segment.from_dict(json.loads(json.dumps(seg.to_dict())))
    assert back.motion == seg.motion
    assert back.transition == seg.transition


def test_segment_without_effects_omits_keys():
    d = Segment(src="a.mp4", in_=0.0, out=1.0).to_dict()
    assert "motion" not in d and "transition" not in d


# --- filter-string composition (fast, mocked ffmpeg) -------------------------

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


def _vf_of(cmd):
    return cmd[cmd.index("-vf") + 1]


def test_no_effect_keeps_plain_scale_pad(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360),
              segments=[Segment(src="a.mp4", in_=0.0, out=1.0)])
    render_edl(edl, "o.mp4")
    enc = next(c for c in calls if "-c:v" in c)
    vf = _vf_of(enc)
    assert vf.startswith("scale=") and "pad=" in vf
    assert "zoompan" not in vf and "fade=" not in vf and "setpts" not in vf
    assert "-af" not in enc


def test_ken_burns_adds_zoompan(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src="a.mp4", in_=0.0, out=2.0,
                motion={"type": "ken_burns", "zoom": 1.15, "direction": "in"})])
    render_edl(edl, "o.mp4")
    vf = _vf_of(next(c for c in calls if "-c:v" in c))
    assert "zoompan" in vf


def test_speed_adds_setpts_and_atempo(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src="a.mp4", in_=0.0, out=2.0,
                motion={"type": "speed", "factor": 2.0})])
    render_edl(edl, "o.mp4")
    enc = next(c for c in calls if "-c:v" in c)
    assert "setpts" in _vf_of(enc)
    assert "-af" in enc and "atempo" in enc[enc.index("-af") + 1]


def test_fade_adds_video_and_audio_fades(monkeypatch):
    calls = _record(monkeypatch)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src="a.mp4", in_=0.0, out=2.0,
                transition={"fade_in": 0.5, "fade_out": 0.5})])
    render_edl(edl, "o.mp4")
    enc = next(c for c in calls if "-c:v" in c)
    vf = _vf_of(enc)
    assert "fade=t=in" in vf and "fade=t=out" in vf
    assert "afade=t=in" in enc[enc.index("-af") + 1]


# --- real renders ------------------------------------------------------------

def test_ken_burns_renders_at_target_dims(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src=str(clip), in_=0.2, out=1.6,
                motion={"type": "ken_burns", "zoom": 1.12, "direction": "in"})])
    out = tmp_path / "o.mp4"
    render_edl(edl, str(out))
    assert _dims(out) == (640, 360)
    assert ffmpeg.duration_of(str(out)) == pytest.approx(1.4, abs=0.3)


def test_speed_halves_duration(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src=str(clip), in_=0.0, out=2.0,
                motion={"type": "speed", "factor": 2.0})])
    out = tmp_path / "o.mp4"
    render_edl(edl, str(out))
    # 2.0s of source at 2x -> ~1.0s output
    assert ffmpeg.duration_of(str(out)) == pytest.approx(1.0, abs=0.3)


def test_fade_segment_renders(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    edl = EDL(fps=30.0, resolution=(640, 360), segments=[
        Segment(src=str(clip), in_=0.0, out=1.5,
                transition={"fade_in": 0.4, "fade_out": 0.4})])
    out = tmp_path / "o.mp4"
    render_edl(edl, str(out))
    assert out.exists()
    assert ffmpeg.duration_of(str(out)) == pytest.approx(1.5, abs=0.3)
