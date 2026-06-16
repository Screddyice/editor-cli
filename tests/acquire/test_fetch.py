import subprocess

import pytest

from editor1.acquire.fetch import (
    FetchError,
    FetchOptions,
    download,
    platform_of,
    resolve_reference,
)


class _Res:
    def __init__(self, stdout):
        self.stdout = stdout


def test_platform_detection():
    assert platform_of("https://www.instagram.com/reel/x/") == "instagram"
    assert platform_of("https://www.tiktok.com/@u/video/1") == "tiktok"
    assert platform_of("https://youtu.be/x") == "youtube"
    assert platform_of("https://example.com/v.mp4") == "other"


def test_cookies_from_browser_added_to_command(tmp_path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res(str(tmp_path / "ID.mp4"))

    resolve_reference(
        "https://www.instagram.com/reel/ID/",
        out_dir=str(tmp_path),
        runner=runner,
        opts=FetchOptions(cookies_from_browser="chrome"),
    )
    assert "--cookies-from-browser" in captured["cmd"]
    assert "chrome" in captured["cmd"]


def test_cookies_file_added_to_command(tmp_path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res(str(tmp_path / "ID.mp4"))

    download("https://www.tiktok.com/@u/video/1", str(tmp_path), runner=runner,
             opts=FetchOptions(cookies_file="/tmp/c.txt"))
    assert "--cookies" in captured["cmd"]
    assert "/tmp/c.txt" in captured["cmd"]


def test_quality_and_section_args(tmp_path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res(str(tmp_path / "ID.mp4"))

    download("https://youtu.be/ID", str(tmp_path), runner=runner,
             opts=FetchOptions(max_height=720, section="*0:00-180"))
    assert "-S" in captured["cmd"] and "res:720" in captured["cmd"]
    assert "--download-sections" in captured["cmd"]
    assert "*0:00-180" in captured["cmd"]


def test_retries_then_succeeds(tmp_path):
    calls = {"n": 0}

    def runner(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise subprocess.CalledProcessError(1, cmd)
        return _Res(str(tmp_path / "ID.mp4"))

    out = download("https://youtu.be/ID", str(tmp_path), runner=runner,
                   opts=FetchOptions(retries=2))
    assert out == str(tmp_path / "ID.mp4")
    assert calls["n"] == 3


def test_instagram_failure_gives_cookie_hint(tmp_path):
    def runner(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    with pytest.raises(FetchError) as exc:
        download("https://www.instagram.com/reel/ID/", str(tmp_path), runner=runner,
                 opts=FetchOptions(retries=0))
    assert "cookies-from-browser" in str(exc.value).lower() or "cookies" in str(exc.value).lower()
