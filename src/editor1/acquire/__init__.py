"""Reference acquisition — resolve a reference (local path or URL) to a file.

Phase 1: local passthrough + yt-dlp (YouTube/TikTok/direct). Phase 2: genre
discovery. Phase 3: cookie auth (Instagram/TikTok) + retry hardening.
"""

from editor1.acquire.fetch import (
    FetchError,
    FetchOptions,
    download,
    platform_of,
    resolve_reference,
)
from editor1.acquire.local import resolve as resolve_local

__all__ = [
    "resolve_reference",
    "download",
    "resolve_local",
    "FetchError",
    "FetchOptions",
    "platform_of",
]
