from pathlib import Path

import pytest

from editor_cli.acquire.internet import (
    AcquisitionError,
    DownloadMetadata,
    InternetAcquirer,
    file_sha256,
)


def public_resolver(_hostname):
    return ("93.184.216.34",)


class FakeDownloader:
    def __init__(
        self,
        *,
        content=b"video",
        filesize=5,
        final_urls=("https://cdn.example.com/reaction.mp4",),
    ):
        self.content = content
        self.metadata = DownloadMetadata(
            filesize=filesize,
            author="Example Creator",
            license_note="CC BY",
            final_urls=final_urls,
        )
        self.download_count = 0

    def inspect(self, _url):
        return self.metadata

    def download(self, _url, destination, _max_bytes):
        self.download_count += 1
        path = destination / f"reaction-{self.download_count}.mp4"
        path.write_bytes(self.content)
        return path


def test_acquire_records_url_hash_and_timeline_use(tmp_path):
    acquirer = InternetAcquirer(
        tmp_path / "assets",
        downloader=FakeDownloader(),
        resolver=public_resolver,
    )
    asset = acquirer.acquire(
        "https://example.com/reaction.mp4", purpose="reaction at 00:12"
    )
    assert asset.source_url == "https://example.com/reaction.mp4"
    assert asset.sha256 == file_sha256(asset.path)
    assert asset.purpose == "reaction at 00:12"
    assert (tmp_path / "assets" / "provenance.jsonl").is_file()


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://host/a", "http://example.com/a"]
)
def test_acquire_rejects_non_https_sources(tmp_path, url):
    with pytest.raises(AcquisitionError, match="HTTPS"):
        InternetAcquirer(
            tmp_path, downloader=FakeDownloader(), resolver=public_resolver
        ).acquire(url, purpose="test")


@pytest.mark.parametrize("url", ["https://127.0.0.1/a", "https://localhost/a"])
def test_acquire_rejects_local_network_sources(tmp_path, url):
    with pytest.raises(AcquisitionError, match="public internet"):
        InternetAcquirer(tmp_path, downloader=FakeDownloader()).acquire(
            url, purpose="test"
        )


def test_acquire_rejects_oversized_metadata(tmp_path):
    downloader = FakeDownloader(filesize=500_000_001)
    with pytest.raises(AcquisitionError, match="500 MB"):
        InternetAcquirer(
            tmp_path, downloader=downloader, resolver=public_resolver
        ).acquire("https://example.com/large.mp4", purpose="test")
    assert downloader.download_count == 0


def test_acquire_rejects_redirect_or_media_url_outside_https(tmp_path):
    downloader = FakeDownloader(final_urls=("http://cdn.example.com/reaction.mp4",))
    with pytest.raises(AcquisitionError, match="redirect"):
        InternetAcquirer(
            tmp_path, downloader=downloader, resolver=public_resolver
        ).acquire("https://example.com/reaction.mp4", purpose="test")


def test_acquire_rejects_executable_signature(tmp_path):
    downloader = FakeDownloader(content=b"\x7fELF" + b"x" * 100)
    with pytest.raises(AcquisitionError, match="executable"):
        InternetAcquirer(
            tmp_path, downloader=downloader, resolver=public_resolver
        ).acquire("https://example.com/reaction.mp4", purpose="test")


def test_acquire_deduplicates_content_by_hash(tmp_path):
    downloader = FakeDownloader(content=b"same-video")
    acquirer = InternetAcquirer(
        tmp_path / "assets", downloader=downloader, resolver=public_resolver
    )
    first = acquirer.acquire("https://example.com/a.mp4", purpose="first")
    second = acquirer.acquire("https://example.com/b.mp4", purpose="second")
    assert second.path == first.path
    assert second.sha256 == first.sha256
    assert second.purpose == "second"
    assert not (tmp_path / "assets" / "reaction-2.mp4").exists()
