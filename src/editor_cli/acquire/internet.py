"""Crash-safe acquisition of direct media from public HTTPS hosts."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, Sequence, TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from editor_cli.direct.media import publish_file

if TYPE_CHECKING:
    from editor_cli.session.models import ExternalAction
    from editor_cli.session.store import SessionStore

ALLOWED_MEDIA_SUFFIXES = frozenset(
    {
        ".aac",
        ".gif",
        ".jpeg",
        ".jpg",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".png",
        ".wav",
        ".webm",
        ".webp",
    }
)
EXECUTABLE_MAGICS = (
    b"\x7fELF",
    b"MZ",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xfe\xed\xfa\xce",
    b"#!",
)
REDIRECTS = frozenset({301, 302, 303, 307, 308})


class AcquisitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadMetadata:
    filesize: int | None = None
    author: str | None = None
    license_note: str | None = None
    final_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadReceipt:
    path: Path
    final_urls: tuple[str, ...]
    connected_addresses: tuple[str, ...]


@dataclass(frozen=True)
class AcquiredAsset:
    path: Path
    source_url: str
    retrieved_at: str
    sha256: str
    purpose: str
    author: str | None = None
    license_note: str | None = None


class Downloader(Protocol):
    def inspect(self, url: str) -> DownloadMetadata: ...
    def download(
        self, url: str, destination: Path, max_bytes: int
    ) -> DownloadReceipt: ...


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_host(hostname: str) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AcquisitionError(
            f"Could not resolve internet media host: {hostname}"
        ) from exc
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _validate_https_shape(url: str) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if parsed.scheme != "https" or not hostname:
        raise AcquisitionError("Internet assets require an HTTPS URL")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise AcquisitionError(
            "Internet asset URLs cannot contain credentials or a non-HTTPS port"
        )


def _public_target(
    url: str, resolver: Callable[[str], Sequence[str]]
) -> tuple[str, tuple[str, ...], str]:
    _validate_https_shape(url)
    parsed = urlsplit(url)
    parsed_hostname = parsed.hostname
    if parsed_hostname is None:  # guarded by _validate_https_shape
        raise AcquisitionError("Internet assets require an HTTPS URL")
    hostname = parsed_hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise AcquisitionError("Internet assets must come from the public internet")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses: tuple[str, ...] = (str(literal),)
    except ValueError:
        addresses = tuple(resolver(hostname))
    try:
        valid = bool(addresses) and all(
            _is_public_address(address) for address in addresses
        )
    except ValueError as exc:
        raise AcquisitionError(
            "Internet media host returned an invalid address"
        ) from exc
    if not valid:
        raise AcquisitionError("Internet assets must come from the public internet")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return hostname, addresses, target


def _require_public_https(url: str, resolver: Callable[[str], Sequence[str]]) -> None:
    _public_target(url, resolver)


def _require_public_peers(addresses: Sequence[str]) -> None:
    try:
        valid = bool(addresses) and all(
            _is_public_address(address) for address in addresses
        )
    except ValueError as exc:
        raise AcquisitionError("Downloader reported an invalid connected peer") from exc
    if not valid:
        raise AcquisitionError("Downloader connected peer must be a public address")


def reject_executable(path: Path) -> None:
    if path.suffix.lower() not in ALLOWED_MEDIA_SUFFIXES:
        raise AcquisitionError(
            f"Unsupported internet media type: {path.suffix or 'none'}"
        )
    with path.open("rb") as handle:
        prefix = handle.read(8)
    if any(prefix.startswith(magic) for magic in EXECUTABLE_MAGICS):
        raise AcquisitionError("Downloaded asset has an executable signature")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        self.tls_context = ssl.create_default_context()
        super().__init__(host, 443, timeout=timeout, context=self.tls_context)
        self.address = address
        self.connected_address = None

    def connect(self) -> None:
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            peer = raw.getpeername()[0]
            if peer != self.address or not _is_public_address(peer):
                raise AcquisitionError(
                    "HTTPS connected peer did not match the pinned public address"
                )
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
            self.connected_address = peer
        except BaseException:
            raw.close()
            raise


class NativeHTTPSDownloader:
    """Direct transport without cookies, proxies, or extractor execution."""

    def __init__(
        self,
        *,
        resolver: Callable[[str], Sequence[str]],
        timeout_seconds: float = 120.0,
    ):
        self.resolver = resolver
        self.timeout_seconds = timeout_seconds

    def inspect(self, _url: str) -> DownloadMetadata:
        return DownloadMetadata()

    def download(self, url: str, destination: Path, max_bytes: int) -> DownloadReceipt:
        deadline = time.monotonic() + self.timeout_seconds
        current = url
        visited: list[str] = []
        peers: list[str] = []
        for redirect_count in range(6):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AcquisitionError("Internet media download timed out")
            host, addresses, target = _public_target(current, self.resolver)
            connection = _PinnedHTTPSConnection(
                host, addresses[0], min(30.0, remaining)
            )
            path: Path | None = None
            try:
                connection.request(
                    "GET",
                    target,
                    headers={"User-Agent": "EditorCLI/0.1", "Accept": "*/*"},
                )
                response = connection.getresponse()
                if connection.connected_address is None:
                    raise AcquisitionError(
                        "HTTPS transport did not report its connected peer"
                    )
                peers.append(connection.connected_address)
                if response.status in REDIRECTS:
                    if redirect_count == 5:
                        raise AcquisitionError("Internet media exceeded five redirects")
                    location = response.getheader("Location")
                    if not location:
                        raise AcquisitionError(
                            "Internet media redirect omitted its location"
                        )
                    current = urljoin(current, location)
                    _require_public_https(current, self.resolver)
                    visited.append(current)
                    continue
                if response.status != 200:
                    raise AcquisitionError(
                        f"Internet media server returned HTTP {response.status}"
                    )
                suffix = Path(urlsplit(current).path).suffix.lower()
                if suffix not in ALLOWED_MEDIA_SUFFIXES:
                    raise AcquisitionError(
                        "Use a direct URL ending in a supported media extension"
                    )
                length = response.getheader("Content-Length")
                if length is not None and int(length) > max_bytes:
                    raise AcquisitionError("Internet asset exceeds the 500 MB limit")
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = destination / ("asset" + suffix)
                count = 0
                with path.open("xb") as handle:
                    while True:
                        if time.monotonic() >= deadline:
                            raise AcquisitionError("Internet media download timed out")
                        if connection.sock is not None:
                            connection.sock.settimeout(
                                min(30.0, max(0.001, deadline - time.monotonic()))
                            )
                        chunk = response.read(min(1024 * 1024, max_bytes + 1 - count))
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > max_bytes:
                            raise AcquisitionError(
                                "Internet asset exceeds the 500 MB limit"
                            )
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not count:
                    raise AcquisitionError(
                        "Internet media server returned an empty file"
                    )
                return DownloadReceipt(path, tuple(visited or (current,)), tuple(peers))
            except (TimeoutError, socket.timeout) as exc:
                if path is not None:
                    path.unlink(missing_ok=True)
                raise AcquisitionError("Internet media download timed out") from exc
            except AcquisitionError:
                if path is not None:
                    path.unlink(missing_ok=True)
                raise
            finally:
                connection.close()
        raise AcquisitionError("Internet media exceeded five redirects")


class InternetAcquirer:
    def __init__(
        self,
        assets_dir: Path,
        *,
        downloader: Downloader | None = None,
        max_bytes: int = 500_000_000,
        resolver: Callable[[str], Sequence[str]] = resolve_host,
        store: SessionStore | None = None,
        timeout_seconds: float = 120.0,
    ):
        self.assets_dir = assets_dir.expanduser().resolve()
        self.assets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.downloader = downloader or NativeHTTPSDownloader(
            resolver=resolver, timeout_seconds=timeout_seconds
        )
        self.max_bytes, self.resolver, self.store = max_bytes, resolver, store
        self.provenance_path = self.assets_dir / "provenance.jsonl"

    def acquire(self, url: str, purpose: str) -> AcquiredAsset:
        _validate_https_shape(url)
        purpose = purpose.strip()
        if not purpose:
            raise AcquisitionError("Internet media requires a timeline purpose")
        staging = self.assets_dir / f".download-{uuid.uuid4().hex}"
        staging.mkdir(mode=0o700)
        token = None
        if self.store is not None:
            token = self.store.begin_external_action(
                "internet.download",
                {"url": url, "purpose": purpose, "staging_path": str(staging)},
                expected={
                    "identity": {"source_url": url},
                    "idempotency": {"source_url": url},
                },
            )
        try:
            _require_public_https(url, self.resolver)
            metadata = self.downloader.inspect(url)
            if metadata.filesize is not None and metadata.filesize > self.max_bytes:
                raise AcquisitionError("Internet asset exceeds the 500 MB limit")
            for final_url in metadata.final_urls:
                try:
                    _require_public_https(final_url, self.resolver)
                except AcquisitionError as exc:
                    raise AcquisitionError(
                        "Internet asset redirect or media URL must remain public HTTPS"
                    ) from exc
            receipt = self.downloader.download(url, staging, self.max_bytes)
            _require_public_peers(receipt.connected_addresses)
            for final_url in receipt.final_urls:
                _require_public_https(final_url, self.resolver)
            path = receipt.path.resolve()
            if not path.is_relative_to(staging) or not path.is_file():
                raise AcquisitionError(
                    "Downloader did not create a media file in its staging directory"
                )
            if path.stat().st_size > self.max_bytes:
                raise AcquisitionError("Internet asset exceeds the 500 MB limit")
            reject_executable(path)
            digest = file_sha256(path)
            existing = self._find_hash(digest)
            final_path = existing or self._publication_path(path.name)
            self._write_receipt(
                staging,
                url,
                purpose,
                path,
                final_path,
                digest,
                author=metadata.author,
                license_note=metadata.license_note,
            )
            if existing is None:
                try:
                    publish_file(path, final_path)
                except OSError as exc:
                    raise AcquisitionError(
                        "Internet asset publication path already exists"
                    ) from exc
                self._fsync_directory(self.assets_dir)
            else:
                path.unlink()
            asset = AcquiredAsset(
                final_path,
                url,
                utc_now(),
                digest,
                purpose,
                metadata.author,
                metadata.license_note,
            )
            self._append_provenance(asset)
            if token is not None and self.store is not None:
                self.store.complete_external_action(token, self.result_for(asset))
            return asset
        except BaseException:
            if not (staging / "receipt.json").is_file():
                for child in staging.iterdir() if staging.exists() else ():
                    child.unlink(missing_ok=True)
                if staging.exists():
                    staging.rmdir()
            raise

    def reconcile(self, action: ExternalAction) -> AcquiredAsset:
        args, expected = action.arguments.thaw(), action.expected.thaw()
        if set(args) != {"url", "purpose", "staging_path"} or set(expected) != {
            "identity",
            "idempotency",
        }:
            raise AcquisitionError("Interrupted download journal schema is invalid")
        raw_identity = expected.get("identity")
        raw_idempotency = expected.get("idempotency")
        if not isinstance(raw_identity, dict) or not isinstance(raw_idempotency, dict):
            raise AcquisitionError("Interrupted download journal schema is invalid")
        identity = dict(raw_identity)
        idempotency = dict(raw_idempotency)
        source_url = args.get("url")
        if (
            identity != {"source_url": source_url}
            or set(idempotency) not in ({"source_url"}, {"source_url", "sha256"})
            or idempotency.get("source_url") != source_url
            or not isinstance(source_url, str)
        ):
            raise AcquisitionError(
                "Interrupted download lacks its exact source URL identity"
            )
        purpose = args.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise AcquisitionError("Interrupted download lacks its timeline purpose")
        staging_value = args.get("staging_path")
        if not isinstance(staging_value, str) or not staging_value:
            raise AcquisitionError("Interrupted download staging path is unsafe")
        try:
            raw_staging = Path(staging_value).expanduser()
            if (
                not raw_staging.is_absolute()
                or raw_staging.is_symlink()
                or raw_staging.resolve() != raw_staging
                or not raw_staging.is_relative_to(self.assets_dir)
            ):
                raise AcquisitionError("Interrupted download staging path is unsafe")
            staging = raw_staging
            row = json.loads((staging / "receipt.json").read_text(encoding="utf-8"))
        except AcquisitionError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AcquisitionError(
                "Interrupted download has no durable exact receipt"
            ) from exc
        required = {
            "source_url",
            "purpose",
            "staged_path",
            "path",
            "sha256",
            "size_bytes",
            "retrieved_at",
            "author",
            "license_note",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise AcquisitionError("Interrupted download receipt schema is invalid")
        string_fields = ("source_url", "purpose", "staged_path", "path", "retrieved_at")
        if any(
            not isinstance(row[field], str) or not row[field] for field in string_fields
        ) or any(
            value is not None and not isinstance(value, str)
            for value in (row["author"], row["license_note"])
        ):
            raise AcquisitionError("Interrupted download receipt values are invalid")
        try:
            final_path = Path(row["path"])
            staged_path = Path(row["staged_path"])
        except (TypeError, ValueError, OSError) as exc:
            raise AcquisitionError(
                "Interrupted download receipt paths are invalid"
            ) from exc
        digest = row["sha256"]
        try:
            if (
                row["source_url"] != source_url
                or row["purpose"] != purpose
                or staged_path != staging / staged_path.name
                or staged_path.parent != staging
                or final_path.parent != self.assets_dir
            ):
                raise AcquisitionError(
                    "Interrupted download receipt does not match its exact URL and paths"
                )
            if final_path.is_symlink() or staged_path.is_symlink():
                raise AcquisitionError(
                    "Interrupted download receipt paths cannot be symlinks"
                )
        except AcquisitionError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise AcquisitionError(
                "Interrupted download receipt paths could not be validated"
            ) from exc
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not isinstance(row["size_bytes"], int)
            or isinstance(row["size_bytes"], bool)
            or row["size_bytes"] <= 0
            or row["size_bytes"] > self.max_bytes
            or not isinstance(row["retrieved_at"], str)
            or not row["retrieved_at"]
        ):
            raise AcquisitionError(
                "Interrupted download receipt content identity is invalid"
            )
        requested_hash = idempotency.get("sha256")
        if requested_hash is not None and requested_hash != digest:
            raise AcquisitionError(
                "Interrupted download receipt does not match its requested hash"
            )
        try:
            if final_path.is_file():
                candidate = final_path
            elif staged_path.is_file() and not staged_path.is_symlink():
                if (
                    staged_path.stat().st_size != row["size_bytes"]
                    or file_sha256(staged_path) != digest
                ):
                    raise AcquisitionError(
                        "Interrupted staged download does not match its exact size and hash"
                    )
                reject_executable(staged_path)
                publish_file(staged_path, final_path)
                self._fsync_directory(self.assets_dir)
                candidate = final_path
            else:
                raise AcquisitionError(
                    "Interrupted download has neither its staged nor published file"
                )
            if (
                candidate.stat().st_size != row["size_bytes"]
                or file_sha256(candidate) != digest
            ):
                raise AcquisitionError(
                    "Interrupted download does not match its exact path, size, and hash"
                )
            reject_executable(candidate)
        except AcquisitionError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise AcquisitionError(
                "Interrupted download evidence could not be validated"
            ) from exc
        asset = AcquiredAsset(
            candidate,
            source_url,
            row["retrieved_at"],
            digest,
            purpose,
            row["author"],
            row["license_note"],
        )
        self._append_provenance(asset)
        return asset

    @staticmethod
    def result_for(asset: AcquiredAsset) -> dict[str, object]:
        return {
            "path": str(asset.path),
            "sha256": asset.sha256,
            "size_bytes": asset.path.stat().st_size,
            "source_url": asset.source_url,
        }

    def _publication_path(self, name: str) -> Path:
        candidate = self.assets_dir / name
        return (
            candidate
            if not candidate.exists()
            else self.assets_dir
            / f"{Path(name).stem}-{uuid.uuid4().hex[:12]}{Path(name).suffix}"
        )

    def _find_hash(self, digest: str) -> Path | None:
        if not self.provenance_path.is_file():
            return None
        for line in self.provenance_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            candidate = Path(row.get("path", "")).resolve()
            if (
                row.get("sha256") == digest
                and candidate.is_relative_to(self.assets_dir)
                and candidate.is_file()
                and file_sha256(candidate) == digest
            ):
                return candidate
        return None

    def _write_receipt(
        self,
        staging: Path,
        source_url: str,
        purpose: str,
        staged_path: Path,
        final_path: Path,
        digest: str,
        *,
        author: str | None = None,
        license_note: str | None = None,
    ) -> None:
        with (staging / "receipt.json").open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "source_url": source_url,
                    "purpose": purpose,
                    "staged_path": str(staged_path),
                    "path": str(final_path),
                    "sha256": digest,
                    "size_bytes": staged_path.stat().st_size,
                    "retrieved_at": utc_now(),
                    "author": author,
                    "license_note": license_note,
                },
                handle,
                sort_keys=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._fsync_directory(staging)

    def _append_provenance(self, asset: AcquiredAsset) -> None:
        row = {**asdict(asset), "path": str(asset.path)}
        if self.provenance_path.is_file():
            for line in self.provenance_path.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line) == row:
                    return
        with self.provenance_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
