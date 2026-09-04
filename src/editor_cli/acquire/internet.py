"""Public-internet media acquisition with bounded downloads and provenance."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlsplit


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
    b"\xfe\xed\fa\xce",
    b"#!",
)


class AcquisitionError(RuntimeError):
    """Raised when an internet asset violates the acquisition boundary."""


@dataclass(frozen=True)
class DownloadMetadata:
    filesize: int | None = None
    author: str | None = None
    license_note: str | None = None
    final_urls: tuple[str, ...] = ()


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

    def download(self, url: str, destination: Path, max_bytes: int) -> Path: ...


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
        raise AcquisitionError(f"Could not resolve internet media host: {hostname}") from exc
    return tuple(dict.fromkeys(record[4][0] for record in records))


def _require_public_https(url: str, resolver: Callable[[str], Sequence[str]]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise AcquisitionError("Internet assets require an HTTPS URL")
    if parsed.username or parsed.password:
        raise AcquisitionError("Internet asset URLs cannot contain credentials")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise AcquisitionError("Internet assets must come from the public internet")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = (str(literal),)
    except ValueError:
        addresses = tuple(resolver(hostname))
    if not addresses:
        raise AcquisitionError("Internet media host did not resolve")
    try:
        public = all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError as exc:
        raise AcquisitionError("Internet media host returned an invalid address") from exc
    if not public:
        raise AcquisitionError("Internet assets must come from the public internet")


def reject_executable(path: Path) -> None:
    if path.suffix.lower() not in ALLOWED_MEDIA_SUFFIXES:
        raise AcquisitionError(f"Unsupported internet media type: {path.suffix or 'none'}")
    with path.open("rb") as handle:
        prefix = handle.read(8)
    if any(prefix.startswith(magic) for magic in EXECUTABLE_MAGICS):
        raise AcquisitionError("Downloaded asset has an executable signature")


class YtDlpDownloader:
    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            message = detail[-1] if detail else "yt-dlp failed"
            raise AcquisitionError(message)
        return completed

    def inspect(self, url: str) -> DownloadMetadata:
        completed = self._run(
            ["yt-dlp", "--dump-single-json", "--no-playlist", "--", url]
        )
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AcquisitionError("yt-dlp returned invalid metadata") from exc
        filesize = value.get("filesize") or value.get("filesize_approx")
        final_urls = tuple(
            dict.fromkeys(
                candidate
                for candidate in (
                    value.get("webpage_url"),
                    value.get("original_url"),
                    value.get("url"),
                )
                if isinstance(candidate, str)
            )
        )
        return DownloadMetadata(
            filesize=int(filesize) if filesize is not None else None,
            author=value.get("uploader") or value.get("creator"),
            license_note=value.get("license"),
            final_urls=final_urls,
        )

    def download(self, url: str, destination: Path, max_bytes: int) -> Path:
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        completed = self._run(
            [
                "yt-dlp",
                "--no-playlist",
                "--restrict-filenames",
                "--max-filesize",
                str(max_bytes),
                "--paths",
                str(destination),
                "--output",
                "%(id)s.%(ext)s",
                "--print",
                "after_move:filepath",
                "--",
                url,
            ]
        )
        paths = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not paths:
            raise AcquisitionError("yt-dlp did not report a downloaded file")
        return Path(paths[-1]).expanduser().resolve()


class InternetAcquirer:
    def __init__(
        self,
        assets_dir: Path,
        *,
        downloader: Downloader | None = None,
        max_bytes: int = 500_000_000,
        resolver: Callable[[str], Sequence[str]] = resolve_host,
    ):
        self.assets_dir = assets_dir.expanduser().resolve()
        self.assets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.downloader = downloader or YtDlpDownloader()
        self.max_bytes = max_bytes
        self.resolver = resolver
        self.provenance_path = self.assets_dir / "provenance.jsonl"

    def acquire(self, url: str, purpose: str) -> AcquiredAsset:
        _require_public_https(url, self.resolver)
        purpose = purpose.strip()
        if not purpose:
            raise AcquisitionError("Internet media requires a timeline purpose")

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

        path = self.downloader.download(url, self.assets_dir, self.max_bytes).resolve()
        if not path.is_relative_to(self.assets_dir):
            raise AcquisitionError("Downloader returned a path outside the session assets")
        try:
            if not path.is_file():
                raise AcquisitionError("Downloader did not create a media file")
            if path.stat().st_size > self.max_bytes:
                raise AcquisitionError("Internet asset exceeds the 500 MB limit")
            reject_executable(path)
        except AcquisitionError:
            path.unlink(missing_ok=True)
            raise

        digest = file_sha256(path)
        existing = self._find_hash(digest)
        duplicate_of: str | None = None
        if existing is not None and existing != path:
            path.unlink()
            path = existing
            duplicate_of = str(existing)

        asset = AcquiredAsset(
            path=path,
            source_url=url,
            retrieved_at=utc_now(),
            sha256=digest,
            purpose=purpose,
            author=metadata.author,
            license_note=metadata.license_note,
        )
        self._append_provenance(asset, duplicate_of=duplicate_of)
        return asset

    def _find_hash(self, digest: str) -> Path | None:
        if not self.provenance_path.is_file():
            return None
        with self.provenance_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                candidate = Path(row["path"]).resolve()
                if (
                    row.get("sha256") == digest
                    and candidate.is_relative_to(self.assets_dir)
                    and candidate.is_file()
                ):
                    return candidate
        return None

    def _append_provenance(
        self, asset: AcquiredAsset, *, duplicate_of: str | None
    ) -> None:
        row = {**asdict(asset), "path": str(asset.path)}
        if duplicate_of is not None:
            row["duplicate_of"] = duplicate_of
        with self.provenance_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
