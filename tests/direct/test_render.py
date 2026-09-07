"""Real offline renderer checks with generated media, including libass-free text."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from editor_cli.direct.render import RenderError, _caption_events, render


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required"
)


def command(*args):
    return subprocess.run(args, capture_output=True, check=True).stdout


def probe(path):
    return json.loads(command("ffprobe", "-v", "error", "-show_streams", "-show_format",
                              "-of", "json", str(path)))


@pytest.fixture
def assets(tmp_path):
    red = tmp_path / "source with quote ' red.mp4"
    blue = tmp_path / "blue.mp4"
    music = tmp_path / "music.wav"
    still = tmp_path / "still.png"
    command("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x90:r=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(red))
    command("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=90x160:r=15",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(blue))
    command("ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=220:sample_rate=32000", "-t", "0.4", str(music))
    Image.new("RGB", (90, 160), (0, 255, 0)).save(still)
    return {key: {"path": str(path), "kind": kind, "metadata": probe(path)}
            for key, path, kind in (("red", red, "video"), ("blue", blue, "video"),
                                    ("still", still, "image"), ("music", music, "audio"))}


def frame(path, timestamp):
    from io import BytesIO
    return Image.open(BytesIO(command("ffmpeg", "-v", "error", "-ss", str(timestamp),
                                     "-i", str(path), "-frames:v", "1", "-f", "image2pipe",
                                     "-c:v", "png", "-"))).convert("RGB")


def white_pixels(image):
    return sum(min(image.getpixel((x, y))) > 190
               for y in range(image.height) for x in range(image.width))


def test_mixed_sources_speed_titles_captions_overlays_music(assets, tmp_path):
    plan = {"width": 320, "height": 180, "fps": 30, "cuts": [
        {"asset_id": "red", "start": 0.2, "end": 1.4, "speed": 2, "reason": "opening"},
        {"asset_id": "blue", "start": 0, "end": 0.8, "kind": "broll", "reason": "second"},
        {"asset_id": "still", "start": 0, "end": 0.4, "kind": "broll", "reason": "ending"}],
        "titles": [{"text": "HELLO [a]; movie=/etc/passwd", "start": 0.05,
                    "duration": 0.3, "position": "center"}],
        "overlays": [{"asset_id": "still", "start": 0.8, "duration": 0.3, "layout": "full"},
                     {"asset_id": "red", "start": 1.15, "duration": 0.15, "layout": "pip"}],
        "captions": True, "music": {"asset_id": "music", "volume": 0.1, "duck": True}}
    transcripts = {"blue": {"words": [{"text": "CAPTION", "start": 0.2, "end": 0.4,
                                       "type": "word"}]}}
    output = render(plan, assets, transcripts, tmp_path / "attempts", preview=True)
    metadata = probe(output)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    audio = next(s for s in metadata["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"], video["r_frame_rate"], video["sample_aspect_ratio"]) == (
        320, 180, "30/1", "1:1")
    assert audio["sample_rate"] == "48000" and audio["channels"] == 2
    assert float(metadata["format"]["duration"]) == pytest.approx(1.8, abs=0.08)
    assert float(audio["duration"]) == pytest.approx(1.8, abs=0.08)
    opening = frame(output, 0.45)
    assert opening.getpixel((160, 90))[0] > 200
    second = frame(output, 0.7)
    assert second.getpixel((160, 90))[2] > 200
    assert max(second.getpixel((10, 90))) < 10  # Portrait is letterboxed.
    overlay = frame(output, 0.9)
    assert overlay.getpixel((160, 70))[1] > 180
    assert white_pixels(overlay) > 50  # Captions remain above the full-frame insert.
    assert white_pixels(frame(output, 0.15)) > 50
    assert white_pixels(frame(output, 1.2)) == 0
    assert frame(output, 1.2).getpixel((250, 45))[0] > 180
    assert frame(output, 1.6).getpixel((160, 90))[1] > 200
    assert not list(output.parent.glob("overlay-*.mp4"))
    assert not (output.parent / "music.mp4").exists()
    timings = json.loads((output.parent / "captions.json").read_text())
    assert timings[0]["start"] == pytest.approx(0.8)
    # The short music asset is looped past its original EOF.
    pcm = command("ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(output), "-t", "0.1",
                  "-vn", "-f", "s16le", "-ac", "1", "-")
    import array
    samples = array.array("h", pcm)
    assert max(abs(s) for s in samples) > 50


def test_fresh_attempts_silent_audio_and_pip(assets, tmp_path):
    plan = {"width": 160, "height": 160, "fps": 24,
            "cuts": [{"asset_id": "blue", "start": 0, "end": 0.4, "speed": 0.5,
                      "grade": "warm", "reason": "silent slow footage"}],
            "overlays": [{"asset_id": "red", "start": 0.2, "duration": 0.3,
                          "source_start": 0.3, "layout": "pip"}]}
    first = render(plan, assets, {}, tmp_path, preview=True)
    original = first.read_bytes()
    second = render(plan, assets, {}, tmp_path, preview=True)
    assert first != second and first.read_bytes() == original
    assert frame(first, 0.3).getpixel((120, 35))[0] > 150
    pcm = command("ffmpeg", "-v", "error", "-i", str(first), "-vn", "-f", "s16le", "-")
    assert set(pcm) == {0}  # Overlay audio does not replace the silent program track.


def test_word_timing_maps_trim_speed_and_ignores_spacing():
    plan = {"cuts": [{"asset_id": "a", "start": 1, "end": 3, "speed": 2, "volume": 1},
                     {"asset_id": "a", "start": 1, "end": 3, "speed": 1, "volume": 0}]}
    words = {"a": {"words": [{"text": "trimmed", "start": 0.8, "end": 1.4},
                               {"text": " ", "start": 1.4, "end": 1.5, "type": "spacing"},
                               {"text": "later", "start": 2, "end": 2.4}]}}
    events = _caption_events(plan, words)
    assert len(events) == 2
    assert events[0]["start"] == 0
    assert events[0]["duration"] == pytest.approx(0.2)
    assert events[1]["start"] == pytest.approx(0.5)


def test_audio_only_cut_has_black_video(assets, tmp_path):
    output = render({"width": 160, "height": 160, "cuts": [
        {"asset_id": "music", "start": 0, "end": 0.3, "reason": "audio passage"}]},
        assets, {}, tmp_path, preview=True)
    assert max(frame(output, 0.1).getpixel((80, 80))) == 0
    assert len(probe(output)["streams"]) == 2


def test_mp4_named_playlist_cannot_read_other_local_media(assets, tmp_path):
    playlist = tmp_path / "disguised.mp4"
    playlist.write_text("ffconcat version 1.0\nfile '" + assets["blue"]["path"] + "'\n")
    assets["disguised"] = {"path": str(playlist), "kind": "video", "metadata": {"streams": []}}
    with pytest.raises(RenderError, match="whitelist"):
        render({"width": 160, "height": 160, "cuts": [
            {"asset_id": "disguised", "start": 0, "end": 0.3, "reason": "must reject"}]},
            assets, {}, tmp_path / "attempts", preview=True)


def test_boosted_cut_audio_is_peak_limited_before_fades(assets, tmp_path):
    import array
    loud = tmp_path / "loud.wav"
    command("ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=1000:sample_rate=48000", "-af", "volume=7",
            "-t", "0.4", str(loud))
    assets["loud"] = {"path": str(loud), "kind": "audio", "metadata": probe(loud)}
    output = render({"width": 160, "height": 160, "cuts": [
        {"asset_id": "loud", "start": 0, "end": 0.4, "volume": 2, "reason": "boost"}]},
        assets, {}, tmp_path / "attempts", preview=True)
    pcm = command("ffmpeg", "-v", "error", "-ss", "0.1", "-i", str(output), "-t", "0.1",
                  "-vn", "-f", "f32le", "-")
    peak = max(abs(value) for value in array.array("f", pcm))
    assert 0.7 < peak < 0.99
