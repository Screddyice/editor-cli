"""Render quality + aspect-ratio guarantees.

The pipeline must (1) encode at near-source quality, not the throwaway
``ultrafast`` preset used for previews, and (2) keep the source clips' real
aspect ratio (9:16 or 16:9, whatever the footage is) — never stretch to a guess.
"""

import json
import subprocess

import pytest

from editor_cli.domain.edl import EDL, Segment
from editor_cli.render import ffmpeg
from editor_cli.render.ffmpeg import render_edl


def _make_clip(path, size="320x240", seconds=2):
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


def test_final_output_matches_source_aspect_ratio(tmp_path):
    # Portrait 9:16 source -> output must stay portrait, not be forced 16:9.
    clip = tmp_path / "src.mp4"
    _make_clip(clip, size="432x768")
    edl = EDL(
        fps=30.0,
        resolution=(1920, 1080),  # a wrong landscape guess; must be ignored
        segments=[Segment(src=str(clip), in_=0.2, out=1.2)],
    )
    out = tmp_path / "out.mp4"
    render_edl(edl, str(out))
    w, h = _dims(out)
    assert (w, h) == (432, 768), f"expected source AR preserved, got {w}x{h}"


def test_mixed_aspect_sources_share_one_frame_without_stretch(tmp_path):
    portrait = tmp_path / "p.mp4"
    landscape = tmp_path / "l.mp4"
    _make_clip(portrait, size="432x768")
    _make_clip(landscape, size="768x432")
    edl = EDL(
        fps=30.0,
        resolution=(100, 100),
        segments=[
            Segment(src=str(portrait), in_=0.1, out=0.9),
            Segment(src=str(landscape), in_=0.1, out=0.9),
        ],
    )
    out = tmp_path / "out.mp4"
    render_edl(edl, str(out))
    # Largest-area source wins the frame; both clips render into it (letterboxed,
    # never stretched). Concat requires a single uniform resolution.
    w, h = _dims(out)
    assert (w, h) in {(432, 768), (768, 432)}
    assert ffmpeg.duration_of(str(out)) == pytest.approx(1.6, abs=0.3)


def test_final_render_uses_quality_encode_not_ultrafast(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd):
        calls.append(cmd)

        class R:
            stdout = json.dumps(
                {"streams": [{"codec_type": "video", "width": 432, "height": 768}],
                 "format": {"duration": "2.0"}}
            )
        return R()

    monkeypatch.setattr(ffmpeg, "_run", fake_run)
    edl = EDL(fps=30.0, resolution=(432, 768),
              segments=[Segment(src="x.mp4", in_=0.0, out=1.0)])
    render_edl(edl, str(tmp_path / "o.mp4"), preview=False)

    encode_cmds = [c for c in calls if "-c:v" in c]
    assert encode_cmds, "expected an encode command"
    enc = encode_cmds[0]
    assert "-crf" in enc, "final render must set an explicit CRF for quality"
    assert "ultrafast" not in enc, "final render must not use the preview ultrafast preset"


def test_preview_render_stays_fast(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd):
        calls.append(cmd)

        class R:
            stdout = json.dumps(
                {"streams": [{"codec_type": "video", "width": 432, "height": 768}],
                 "format": {"duration": "2.0"}}
            )
        return R()

    monkeypatch.setattr(ffmpeg, "_run", fake_run)
    edl = EDL(fps=30.0, resolution=(432, 768),
              segments=[Segment(src="x.mp4", in_=0.0, out=1.0)])
    render_edl(edl, str(tmp_path / "o.mp4"), preview=True)

    enc = next(c for c in calls if "-c:v" in c)
    assert "ultrafast" in enc, "preview should stay on the fast preset"
