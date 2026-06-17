"""Genre / trend discovery via yt-dlp search.

``discover_genre`` finds comparable videos for a query; ``fetch_sound_meta``
pulls each result's title/track/artist; ``trend_summary`` renders a text block
the style analysis can read. Headless YouTube now; Instagram/TikTok + HyperCrawl
richer discovery is Phase 3. ``runner`` is injectable so tests run offline.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

RunnerFn = Callable[..., Any]


@dataclass
class SoundMeta:
    title: str
    track: str | None
    artist: str | None
    url: str


def discover_genre(query: str, n: int = 5, runner: RunnerFn = subprocess.run) -> list[str]:
    res = runner(
        ["yt-dlp", f"ytsearch{n}:{query}", "--print", "webpage_url", "--no-download"],
        check=True, capture_output=True, text=True,
    )
    out = (getattr(res, "stdout", "") or "").strip()
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch_sound_meta(url: str, runner: RunnerFn = subprocess.run) -> SoundMeta:
    res = runner(
        ["yt-dlp", "--dump-json", "--no-download", url],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(getattr(res, "stdout", "") or "{}")
    return SoundMeta(
        title=data.get("title", ""),
        track=data.get("track"),
        artist=data.get("artist") or data.get("uploader"),
        url=url,
    )


def trend_summary(metas: list[SoundMeta]) -> str:
    if not metas:
        return ""
    lines = []
    for m in metas:
        sound = f"  (sound: {m.track} — {m.artist})" if m.track else ""
        lines.append(f"- {m.title}{sound}")
    return "GENRE TREND REFERENCES (match this style):\n" + "\n".join(lines)
