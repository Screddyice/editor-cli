import json

from editor1.analysis.transcribe import Transcript, transcribe


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_transcribe_parses_words_and_filters_events(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFxxxx")
    captured = {}

    def fake_post(url, headers, data, files):
        captured.update(url=url, headers=headers, data=data)
        return _FakeResp(
            {"words": [
                {"text": "hello", "start": 0.0, "end": 0.4, "type": "word"},
                {"text": "world", "start": 0.5, "end": 0.9, "type": "word"},
                {"text": "(laughs)", "start": 0.9, "end": 1.0, "type": "audio_event"},
            ]}
        )

    def fake_extract(video, out_wav):
        return str(audio)

    t = transcribe("video.mp4", "KEY", out_dir=str(tmp_path),
                   post=fake_post, extract_audio=fake_extract)
    assert isinstance(t, Transcript)
    assert [w.text for w in t.words] == ["hello", "world"]  # event filtered out
    assert captured["headers"]["xi-api-key"] == "KEY"
    assert captured["data"]["model_id"] == "scribe_v1"
    assert t.text == "hello world"


def test_transcribe_uses_cache(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "video.json").write_text(
        json.dumps({"words": [{"text": "cached", "start": 0, "end": 1, "type": "word"}]})
    )

    def boom(*a, **k):
        raise AssertionError("network/ffmpeg should not run on cache hit")

    t = transcribe("video.mp4", "K", out_dir=str(tmp_path), post=boom, extract_audio=boom)
    assert [w.text for w in t.words] == ["cached"]
