import pytest

from editor1.acquire import resolve_reference


def test_local_passthrough(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    assert resolve_reference(str(f)) == str(f)


def test_missing_local_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_reference(str(tmp_path / "nope.mp4"))


def test_url_invokes_yt_dlp(tmp_path):
    captured = {}
    final_path = str(tmp_path / "ID.mp4")

    class _Res:
        stdout = final_path

    def fake_runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res()

    out = resolve_reference(
        "https://youtu.be/ID", out_dir=str(tmp_path), runner=fake_runner
    )
    assert out == final_path
    assert captured["cmd"][0] == "yt-dlp"
    assert "https://youtu.be/ID" in captured["cmd"]


def test_url_without_out_dir_raises():
    with pytest.raises(ValueError):
        resolve_reference("https://youtu.be/ID")
