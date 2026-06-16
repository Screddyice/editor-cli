import subprocess

import pytest

from editor1.domain.edl import EDL, Segment
from editor1.render.ffmpeg import duration_of, probe, render_edl


def _make_clip(path, seconds=3):
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def test_probe_reports_duration(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    assert duration_of(str(clip)) == pytest.approx(3.0, abs=0.2)
    info = probe(str(clip))
    assert any(s["codec_type"] == "video" for s in info["streams"])


def test_render_edl_cuts_to_expected_duration(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_clip(clip)
    edl = EDL(
        fps=30.0,
        resolution=(320, 240),
        segments=[Segment(src=str(clip), in_=0.5, out=1.5)],
    )
    out = tmp_path / "out.mp4"
    render_edl(edl, str(out))
    assert out.exists()
    assert duration_of(str(out)) == pytest.approx(1.0, abs=0.2)
