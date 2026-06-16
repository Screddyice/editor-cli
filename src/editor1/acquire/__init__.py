"""Reference acquisition — resolve a reference (local path or URL) to a file.

Phase 1: local passthrough + yt-dlp (YouTube/TikTok/direct). HyperCrawl genre
discovery and Instagram cookie auth arrive in Phases 2 and 3.
"""

from editor1.acquire.fetch import FetchError, download, resolve_reference
from editor1.acquire.local import resolve as resolve_local

__all__ = ["resolve_reference", "download", "resolve_local", "FetchError"]
