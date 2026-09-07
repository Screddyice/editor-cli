import json
import socket

import pytest

from editor_cli.acquire.internet import (
    AcquisitionError,
    DownloadReceipt,
    DownloadMetadata,
    InternetAcquirer,
    NativeHTTPSDownloader,
    file_sha256,
)
from editor_cli.session.models import ExternalAction
from editor_cli.session.store import SessionStore


def public_resolver(_hostname):
    return ("93.184.216.34",)


class FakeDownloader:
    def __init__(
        self,
        *,
        content=b"video",
        filesize=5,
        final_urls=("https://cdn.example.com/reaction.mp4",),
        connected_addresses=("93.184.216.34",),
    ):
        self.content = content
        self.metadata = DownloadMetadata(
            filesize=filesize,
            author="Example Creator",
            license_note="CC BY",
            final_urls=final_urls,
        )
        self.download_count = 0
        self.connected_addresses = connected_addresses
        self.download_destinations = []
        self.on_inspect = None

    def inspect(self, _url):
        if self.on_inspect is not None:
            self.on_inspect()
        return self.metadata

    def download(self, _url, destination, _max_bytes):
        self.download_count += 1
        self.download_destinations.append(destination)
        path = destination / f"reaction-{self.download_count}.mp4"
        path.write_bytes(self.content)
        return DownloadReceipt(
            path=path,
            final_urls=self.metadata.final_urls,
            connected_addresses=self.connected_addresses,
        )


def test_acquire_records_url_hash_and_timeline_use(tmp_path):
    downloader = FakeDownloader()
    acquirer = InternetAcquirer(
        tmp_path / "assets",
        downloader=downloader,
        resolver=public_resolver,
    )
    asset = acquirer.acquire(
        "https://example.com/reaction.mp4", purpose="reaction at 00:12"
    )
    assert asset.source_url == "https://example.com/reaction.mp4"
    assert asset.sha256 == file_sha256(asset.path)
    assert asset.purpose == "reaction at 00:12"
    assert (tmp_path / "assets" / "provenance.jsonl").is_file()
    assert ".download-" in downloader.download_destinations[0].name
    assert ".download-" not in asset.path.name


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://host/a",
        "http://example.com/a",
    ],
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


def test_acquire_rejects_non_public_connected_peer(tmp_path):
    downloader = FakeDownloader(connected_addresses=("127.0.0.1",))
    with pytest.raises(AcquisitionError, match="connected peer"):
        InternetAcquirer(
            tmp_path, downloader=downloader, resolver=public_resolver
        ).acquire("https://example.com/reaction.mp4", purpose="test")


def test_acquire_rejects_executable_signature(tmp_path):
    downloader = FakeDownloader(content=b"\x7fELF" + b"x" * 100)
    with pytest.raises(AcquisitionError, match="executable"):
        InternetAcquirer(
            tmp_path, downloader=downloader, resolver=public_resolver
        ).acquire("https://example.com/reaction.mp4", purpose="test")


def test_acquire_rejects_big_endian_32_bit_mach_o_signature(tmp_path):
    downloader = FakeDownloader(content=b"\xfe\xed\xfa\xce" + b"x" * 100)
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
    assert len(tuple((tmp_path / "assets").glob("reaction-*.mp4"))) == 1


def test_acquire_journals_before_network_access_and_reconciles_by_url_hash(tmp_path):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    downloader = FakeDownloader(content=b"journaled-video")
    downloader.on_inspect = lambda: (
        (store.pending_actions()[0].action == "internet.download")
        or (_ for _ in ()).throw(AssertionError("download was not journaled"))
    )
    acquirer = InternetAcquirer(
        assets,
        downloader=downloader,
        resolver=public_resolver,
        store=store,
    )

    asset = acquirer.acquire("https://example.com/a.mp4", purpose="reaction")
    assert store.pending_actions() == []

    staging = downloader.download_destinations[0]
    token = store.begin_external_action(
        "internet.download",
        {
            "url": asset.source_url,
            "purpose": asset.purpose,
            "staging_path": str(staging),
        },
        expected_identity={"source_url": asset.source_url},
        idempotency={"source_url": asset.source_url, "sha256": asset.sha256},
    )
    reconciled = acquirer.reconcile(store.pending_actions()[0])

    assert reconciled.sha256 == asset.sha256
    store.complete_external_action(token, {"path": str(reconciled.path)})


def test_acquire_journals_before_dns_resolution(tmp_path):
    store = SessionStore(tmp_path)

    def resolver(_host):
        assert store.pending_actions()[0].action == "internet.download"
        return public_resolver(_host)

    InternetAcquirer(
        tmp_path / "assets",
        downloader=FakeDownloader(),
        resolver=resolver,
        store=store,
    ).acquire("https://example.com/a.mp4", purpose="reaction")


def test_stale_provenance_hash_does_not_deduplicate_changed_file(tmp_path):
    downloader = FakeDownloader(content=b"same-video")
    acquirer = InternetAcquirer(
        tmp_path / "assets", downloader=downloader, resolver=public_resolver
    )
    first = acquirer.acquire("https://example.com/a.mp4", purpose="first")
    first.path.write_bytes(b"changed")

    second = acquirer.acquire("https://example.com/b.mp4", purpose="second")

    assert second.path != first.path
    assert second.path.read_bytes() == b"same-video"


def test_reconcile_finishes_exact_staged_publication_without_network(tmp_path):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    acquirer = InternetAcquirer(assets, downloader=FakeDownloader(), store=store)
    staging = assets / ".download-crashed"
    staging.mkdir()
    staged = staging / "asset.mp4"
    staged.write_bytes(b"video")
    final = assets / "asset.mp4"
    digest = file_sha256(staged)
    acquirer._write_receipt(
        staging, "https://example.com/a.mp4", "reaction", staged, final, digest
    )
    store.begin_external_action(
        "internet.download",
        {
            "url": "https://example.com/a.mp4",
            "purpose": "reaction",
            "staging_path": str(staging),
        },
        expected_identity={"source_url": "https://example.com/a.mp4"},
        idempotency={"source_url": "https://example.com/a.mp4", "sha256": digest},
    )

    asset = acquirer.reconcile(store.pending_actions()[0])

    assert asset.path == final
    assert final.read_bytes() == b"video"
    assert not staged.exists()
    assert '"path": "' + str(final) + '"' in acquirer.provenance_path.read_text()


def test_reconcile_restores_attribution_for_deduplicated_content(tmp_path):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    acquirer = InternetAcquirer(assets, downloader=FakeDownloader(), store=store)
    existing = assets / "existing.mp4"
    existing.write_bytes(b"video")
    staging = assets / ".download-crashed"
    staging.mkdir()
    staged = staging / "asset.mp4"
    staged.write_bytes(b"video")
    digest = file_sha256(staged)
    acquirer._write_receipt(
        staging,
        "https://example.com/a.mp4",
        "reaction",
        staged,
        existing,
        digest,
        author="Example Creator",
        license_note="CC BY",
    )
    staged.unlink()
    store.begin_external_action(
        "internet.download",
        {
            "url": "https://example.com/a.mp4",
            "purpose": "reaction",
            "staging_path": str(staging),
        },
        expected_identity={"source_url": "https://example.com/a.mp4"},
        idempotency={"source_url": "https://example.com/a.mp4", "sha256": digest},
    )

    asset = acquirer.reconcile(store.pending_actions()[0])

    assert asset.author == "Example Creator"
    assert asset.license_note == "CC BY"
    provenance = acquirer.provenance_path.read_text(encoding="utf-8")
    assert '"author": "Example Creator"' in provenance
    assert '"license_note": "CC BY"' in provenance


@pytest.mark.parametrize(
    ("content", "suffix", "message"),
    [
        (b"", ".mp4", "content identity"),
        (b"\x7fELFpayload", ".mp4", "executable"),
        (b"video", ".bin", "Unsupported internet media type"),
    ],
)
def test_reconcile_reapplies_media_safety_checks(tmp_path, content, suffix, message):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    acquirer = InternetAcquirer(assets, downloader=FakeDownloader(), store=store)
    staging = assets / ".download-crashed"
    staging.mkdir()
    staged = staging / f"asset{suffix}"
    staged.write_bytes(content)
    final = assets / f"asset{suffix}"
    digest = file_sha256(staged)
    acquirer._write_receipt(
        staging, "https://example.com/a.mp4", "reaction", staged, final, digest
    )
    store.begin_external_action(
        "internet.download",
        {
            "url": "https://example.com/a.mp4",
            "purpose": "reaction",
            "staging_path": str(staging),
        },
        expected_identity={"source_url": "https://example.com/a.mp4"},
        idempotency={"source_url": "https://example.com/a.mp4", "sha256": digest},
    )

    with pytest.raises(AcquisitionError, match=message):
        acquirer.reconcile(store.pending_actions()[0])


@pytest.mark.parametrize("field", ["path", "staged_path", "size_bytes"])
def test_reconcile_wraps_malformed_receipt_values(tmp_path, field):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    acquirer = InternetAcquirer(assets, downloader=FakeDownloader(), store=store)
    staging = assets / ".download-crashed"
    staging.mkdir()
    staged = staging / "asset.mp4"
    staged.write_bytes(b"video")
    final = assets / "asset.mp4"
    digest = file_sha256(staged)
    acquirer._write_receipt(
        staging, "https://example.com/a.mp4", "reaction", staged, final, digest
    )
    receipt_path = staging / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = None
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    store.begin_external_action(
        "internet.download",
        {
            "url": "https://example.com/a.mp4",
            "purpose": "reaction",
            "staging_path": str(staging),
        },
        expected_identity={"source_url": "https://example.com/a.mp4"},
        idempotency={"source_url": "https://example.com/a.mp4", "sha256": digest},
    )

    with pytest.raises(AcquisitionError):
        acquirer.reconcile(store.pending_actions()[0])


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            {
                "url": "https://example.com/a.mp4",
                "purpose": "reaction",
                "staging_path": None,
            },
            {
                "identity": {"source_url": "https://example.com/a.mp4"},
                "idempotency": {"source_url": "https://example.com/a.mp4"},
            },
        ),
        (
            {
                "url": "https://example.com/a.mp4",
                "purpose": "reaction",
                "staging_path": "/tmp/staging",
            },
            {"identity": None, "idempotency": None},
        ),
    ],
)
def test_reconcile_wraps_malformed_journal_mappings(tmp_path, arguments, expected):
    action = ExternalAction(
        token="token",
        action="internet.download",
        arguments=arguments,
        expected=expected,
        status="pending",
    )

    with pytest.raises(AcquisitionError):
        InternetAcquirer(tmp_path / "assets").reconcile(action)


def test_reconcile_wraps_oversized_receipt_path(tmp_path):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    acquirer = InternetAcquirer(assets, downloader=FakeDownloader(), store=store)
    staging = assets / ".download-crashed"
    staging.mkdir()
    staged = staging / "asset.mp4"
    staged.write_bytes(b"video")
    final = assets / "asset.mp4"
    digest = file_sha256(staged)
    acquirer._write_receipt(
        staging, "https://example.com/a.mp4", "reaction", staged, final, digest
    )
    receipt_path = staging / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["path"] = str(assets / ("x" * 10_000))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    store.begin_external_action(
        "internet.download",
        {
            "url": "https://example.com/a.mp4",
            "purpose": "reaction",
            "staging_path": str(staging),
        },
        expected_identity={"source_url": "https://example.com/a.mp4"},
        idempotency={"source_url": "https://example.com/a.mp4", "sha256": digest},
    )

    with pytest.raises(AcquisitionError):
        acquirer.reconcile(store.pending_actions()[0])


def test_reconcile_preserves_staged_file_when_publication_conflicts(tmp_path):
    assets = tmp_path / "assets"
    store = SessionStore(tmp_path)
    acquirer = InternetAcquirer(assets, downloader=FakeDownloader(), store=store)
    staging = assets / ".download-crashed"
    staging.mkdir()
    staged = staging / "asset.mp4"
    staged.write_bytes(b"wanted")
    final = assets / "asset.mp4"
    final.write_bytes(b"competitor")
    digest = file_sha256(staged)
    acquirer._write_receipt(
        staging, "https://example.com/a.mp4", "reaction", staged, final, digest
    )
    store.begin_external_action(
        "internet.download",
        {
            "url": "https://example.com/a.mp4",
            "purpose": "reaction",
            "staging_path": str(staging),
        },
        expected_identity={"source_url": "https://example.com/a.mp4"},
        idempotency={"source_url": "https://example.com/a.mp4", "sha256": digest},
    )

    with pytest.raises(AcquisitionError, match="exact path, size, and hash"):
        acquirer.reconcile(store.pending_actions()[0])
    assert staged.read_bytes() == b"wanted"
    assert final.read_bytes() == b"competitor"


@pytest.mark.parametrize(
    "address",
    [
        "100.64.0.1",
        "100.127.255.254",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
    ],
)
def test_acquire_rejects_non_unicast_special_addresses(tmp_path, address):
    with pytest.raises(AcquisitionError, match="public internet"):
        InternetAcquirer(
            tmp_path,
            downloader=FakeDownloader(),
            resolver=lambda _host: (address,),
        ).acquire("https://example.com/a.mp4", purpose="reaction")


class FakeResponse:
    def __init__(self, status, *, location=None, chunks=()):
        self.status = status
        self.location = location
        self.chunks = iter(chunks)

    def getheader(self, name):
        return self.location if name == "Location" else None

    def read(self, _size):
        value = next(self.chunks, b"")
        if isinstance(value, BaseException):
            raise value
        return value


class FakeConnection:
    responses = []

    def __init__(self, _host, address, _timeout):
        self.connected_address = address
        self.sock = None

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        pass


def test_native_transport_revalidates_redirect_before_next_connection(
    tmp_path, monkeypatch
):
    import editor_cli.acquire.internet as internet

    FakeConnection.responses = [
        FakeResponse(302, location="https://internal.example/a.mp4")
    ]
    monkeypatch.setattr(internet, "_PinnedHTTPSConnection", FakeConnection)

    def resolver(host):
        return ("127.0.0.1",) if host == "internal.example" else public_resolver(host)

    downloader = NativeHTTPSDownloader(resolver=resolver)

    with pytest.raises(AcquisitionError, match="public internet"):
        downloader.download("https://example.com/a.mp4", tmp_path, 500_000_000)


def test_native_transport_converts_socket_timeout_and_leaves_no_file(
    tmp_path, monkeypatch
):
    import editor_cli.acquire.internet as internet

    FakeConnection.responses = [FakeResponse(200, chunks=(socket.timeout(),))]
    monkeypatch.setattr(internet, "_PinnedHTTPSConnection", FakeConnection)
    downloader = NativeHTTPSDownloader(resolver=public_resolver)

    with pytest.raises(AcquisitionError, match="timed out"):
        downloader.download("https://example.com/a.mp4", tmp_path, 500_000_000)
    assert not list(tmp_path.glob("*.mp4"))
