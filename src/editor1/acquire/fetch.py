"""URL reference fetch via yt-dlp; dispatcher over local vs URL."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from editor1.acquire import local

RunnerFn = Callable[..., Any]


class FetchError(RuntimeError):
    pass


def _is_url(ref: str) -> bool:
    return ref.startswith("http://") or ref.startswith("https://")


def download(url: str, out_dir: str, runner: RunnerFn = subprocess.run) -> str:
    """Download a video with yt-dlp; return the final file path."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    template = os.path.join(out_dir, "%(id)s.%(ext)s")
    res = runner(
        ["yt-dlp", "--no-progress", "--print", "after_move:filepath",
         "-o", template, url],
        check=True, capture_output=True, text=True,
    )
    out = (getattr(res, "stdout", "") or "").strip()
    if not out:
        raise FetchError(f"yt-dlp produced no output path for {url}")
    return out.splitlines()[-1]


def resolve_reference(
    ref: str,
    out_dir: str | None = None,
    runner: RunnerFn = subprocess.run,
) -> str:
    if _is_url(ref):
        if not out_dir:
            raise ValueError("out_dir is required to fetch a URL reference")
        return download(ref, out_dir, runner=runner)
    return local.resolve(ref)
