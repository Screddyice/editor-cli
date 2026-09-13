import socket

import pytest

from editor_cli.direct.media import public_target, download, publish_file


def addresses(monkeypatch, ips):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips
        ],
    )


def test_public_target_rejects_private_and_mixed_dns(monkeypatch):
    for ips in (["127.0.0.1"], ["169.254.169.254"], ["8.8.8.8", "10.0.0.1"]):
        addresses(monkeypatch, ips)
        with pytest.raises(ValueError, match="public"):
            public_target("https://example.com/video.mp4")
    addresses(monkeypatch, ["8.8.8.8"])
    assert public_target("https://example.com/video.mp4?x=1") == (
        "example.com",
        "8.8.8.8",
        "/video.mp4?x=1",
    )
    for url in [
        "http://example.com/a.mp4",
        "https://user:pass@example.com/a.mp4",
        "file:///tmp/video.mp4",
        "https://example.com:8080/a.mp4",
    ]:
        with pytest.raises(ValueError):
            public_target(url)


def test_redirect_to_private_never_connects(monkeypatch, tmp_path):
    addresses(monkeypatch, ["8.8.8.8"])
    calls = []

    class Response:
        status = 302

        def getheader(self, name):
            addresses(monkeypatch, ["127.0.0.1"])
            return "https://localhost/private.mp4"

    class Connection:
        def __init__(self, host, address):
            calls.append((host, address))

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("editor_cli.direct.media.PinnedHTTPS", Connection)
    with pytest.raises(ValueError, match="public"):
        download("https://example.com/a.mp4", tmp_path / "asset")
    assert calls == [("example.com", "8.8.8.8")]


def test_download_enforces_stream_limit(monkeypatch, tmp_path):
    addresses(monkeypatch, ["8.8.8.8"])

    class Response:
        status = 200

        def getheader(self, name):
            return None

        def read(self, n):
            return b"0123456789"

    class Connection:
        def __init__(self, *a):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("editor_cli.direct.media.PinnedHTTPS", Connection)
    with pytest.raises(ValueError, match="500 MB"):
        download("https://example.com/a.mp4", tmp_path / "asset", max_bytes=9)


def test_publication_never_overwrites_and_needs_no_hardlinks(tmp_path, monkeypatch):
    import os

    def unsupported(*args):
        raise OSError("hard links unavailable on removable media")

    monkeypatch.setattr(os, "link", unsupported)
    staging, final = tmp_path / "staging.mp4", tmp_path / "final.mp4"
    staging.write_bytes(b"reviewed")
    publish_file(staging, final)
    assert final.read_bytes() == b"reviewed" and not staging.exists()
    staging.write_bytes(b"different")
    with pytest.raises(FileExistsError):
        publish_file(staging, final)
    assert final.read_bytes() == b"reviewed" and staging.read_bytes() == b"different"
