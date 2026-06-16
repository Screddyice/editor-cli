import json

from editor1.acquire.discover import (
    SoundMeta,
    discover_genre,
    fetch_sound_meta,
    trend_summary,
)


class _Res:
    def __init__(self, stdout):
        self.stdout = stdout


def test_discover_genre_returns_urls_and_uses_ytsearch():
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res("https://youtu.be/a\nhttps://youtu.be/b\n\n")

    urls = discover_genre("edm launch reel", n=2, runner=runner)
    assert urls == ["https://youtu.be/a", "https://youtu.be/b"]
    assert captured["cmd"][1] == "ytsearch2:edm launch reel"


def test_fetch_sound_meta_parses_json():
    payload = {"title": "Big Launch", "track": "Pump It", "artist": "DJ X",
               "uploader": "ChannelY"}

    def runner(cmd, **kwargs):
        return _Res(json.dumps(payload))

    meta = fetch_sound_meta("https://youtu.be/a", runner=runner)
    assert isinstance(meta, SoundMeta)
    assert meta.title == "Big Launch" and meta.track == "Pump It"
    assert meta.artist == "DJ X" and meta.url == "https://youtu.be/a"


def test_fetch_sound_meta_falls_back_to_uploader():
    def runner(cmd, **kwargs):
        return _Res(json.dumps({"title": "T", "uploader": "Creator"}))

    meta = fetch_sound_meta("u", runner=runner)
    assert meta.track is None and meta.artist == "Creator"


def test_trend_summary_formats():
    metas = [
        SoundMeta(title="A", track="Song1", artist="X", url="u1"),
        SoundMeta(title="B", track=None, artist="Y", url="u2"),
    ]
    out = trend_summary(metas)
    assert "GENRE TREND REFERENCES" in out
    assert "Song1" in out and "- B" in out


def test_trend_summary_empty():
    assert trend_summary([]) == ""
