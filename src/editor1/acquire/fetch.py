"""URL reference fetch via yt-dlp; dispatcher over local vs URL.

Phase 3 adds cookie auth (browser or file) for login-gated platforms
(Instagram/TikTok) and retry with an actionable error. ``runner`` is injectable
so tests run offline.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from editor1.acquire import local

RunnerFn = Callable[..., Any]


class FetchError(RuntimeError):
    pass


@dataclass
class FetchOptions:
    cookies_from_browser: Optional[str] = None  # e.g. "chrome", "safari", "firefox"
    cookies_file: Optional[str] = None
    retries: int = 2
    max_height: Optional[int] = None  # cap download resolution (e.g. 720)
    section: Optional[str] = None  # yt-dlp download-sections spec, e.g. "*0:00-4:00"


def _is_url(ref: str) -> bool:
    return ref.startswith("http://") or ref.startswith("https://")


def platform_of(url: str) -> str:
    u = url.lower()
    if "instagram.com" in u:
        return "instagram"
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "other"


def _cookie_args(opts: FetchOptions) -> list[str]:
    if opts.cookies_from_browser:
        return ["--cookies-from-browser", opts.cookies_from_browser]
    if opts.cookies_file:
        return ["--cookies", opts.cookies_file]
    return []


def _quality_args(opts: FetchOptions) -> list[str]:
    args: list[str] = []
    if opts.max_height:
        args += ["-S", f"res:{opts.max_height}"]
    if opts.section:
        args += ["--download-sections", opts.section, "--force-keyframes-at-cuts"]
    return args


def download(
    url: str,
    out_dir: str,
    runner: RunnerFn = subprocess.run,
    opts: Optional[FetchOptions] = None,
) -> str:
    """Download a video with yt-dlp; return the final file path. Retries on failure."""
    opts = opts or FetchOptions()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    template = os.path.join(out_dir, "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp", "--no-progress", "--print", "after_move:filepath",
        *_cookie_args(opts), *_quality_args(opts), "-o", template, url,
    ]
    last_err: Any = None
    for _ in range(opts.retries + 1):
        try:
            res = runner(cmd, check=True, capture_output=True, text=True)
            out = (getattr(res, "stdout", "") or "").strip()
            if out:
                return out.splitlines()[-1]
            last_err = FetchError("yt-dlp produced no output path")
        except Exception as exc:  # noqa: BLE001 — retry on any runner failure
            last_err = exc

    plat = platform_of(url)
    hint = ""
    if plat in ("instagram", "tiktok") and not _cookie_args(opts):
        hint = (
            f" {plat.title()} usually needs login cookies — pass "
            "--cookies-from-browser <chrome|safari|firefox> or --cookies <file>."
        )
    raise FetchError(f"Failed to fetch {url} ({plat}): {last_err}.{hint}")


def resolve_reference(
    ref: str,
    out_dir: str | None = None,
    runner: RunnerFn = subprocess.run,
    opts: Optional[FetchOptions] = None,
) -> str:
    if _is_url(ref):
        if not out_dir:
            raise ValueError("out_dir is required to fetch a URL reference")
        return download(ref, out_dir, runner=runner, opts=opts)
    return local.resolve(ref)
