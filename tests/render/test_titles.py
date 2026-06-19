"""Auto-applied titles — the editor's titles get burned onto the render."""

import subprocess

import pytest

from editor_cli.render.ffmpeg import _title_layout, apply_titles, duration_of, probe


def _clip(path, size="640x360", seconds=3):
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def test_layout_none_without_text():
    assert _title_layout({"start": 0}) is None


def test_layout_tolerant_shape_and_positions():
    top = _title_layout({"text": "Hi", "start": 1, "end": 3, "position": "top"})
    assert top["text"] == "Hi" and top["start"] == 1 and top["end"] == 3
    assert top["region"] == "top"

    assert _title_layout({"label": "Yo"})["region"] == "bottom"  # label alias, default region
    assert _title_layout({"content": "C", "position": "center"})["region"] == "center"

    # duration -> end, and end<=start guard
    assert _title_layout({"text": "x", "start": 2, "duration": 1.5})["end"] == 3.5


def test_apply_titles_burns_in_and_preserves_dims_duration_audio(tmp_path):
    v = tmp_path / "v.mp4"
    _clip(v)
    out = tmp_path / "o.mp4"
    apply_titles(
        str(v),
        [{"text": "Hello world", "start": 0.2, "end": 1.5, "position": "bottom"}],
        str(out),
    )
    info = probe(str(out))
    vid = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(vid["width"]), int(vid["height"])) == (640, 360)
    assert duration_of(str(out)) == pytest.approx(3.0, abs=0.3)
    assert any(s["codec_type"] == "audio" for s in info["streams"])


def test_apply_titles_passthrough_when_nothing_drawable(tmp_path):
    v = tmp_path / "v.mp4"
    _clip(v)
    out = tmp_path / "o.mp4"
    apply_titles(str(v), [{"start": 0}], str(out))  # no text
    assert out.exists()
    assert duration_of(str(out)) == pytest.approx(3.0, abs=0.3)
