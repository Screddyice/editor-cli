"""ffmpeg render — turn an EDL into an mp4, plus an ffprobe media manifest.

Each segment is seek-extracted and re-encoded to a uniform format/resolution so
the parts concat cleanly (copy concat). Preview mode renders 1280x720 fast.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from editor_cli.domain.edl import EDL


class RenderError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RenderError(f"{cmd[0]} failed (exit {res.returncode}): {res.stderr[-2000:]}")
    return res


def probe(path: str) -> dict:
    """ffprobe manifest: format + streams as a dict."""
    res = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path]
    )
    return json.loads(res.stdout)


def duration_of(path: str) -> float:
    return float(probe(path)["format"]["duration"])


def render_edl(edl: EDL, out: str, preview: bool = False) -> str:
    fps = edl.fps
    tw, th = (1280, 720) if preview else edl.resolution
    tmp = tempfile.mkdtemp(prefix="editor_cli_render_")
    parts: list[str] = []
    for i, seg in enumerate(edl.segments):
        part = os.path.join(tmp, f"part{i:04d}.mp4")
        _run(
            ["ffmpeg", "-y",
             "-ss", str(seg.in_), "-i", seg.src, "-t", str(seg.duration),
             "-vf", f"scale={tw}:{th}", "-r", str(fps),
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ac", "2", "-ar", "48000",
             part]
        )
        parts.append(part)
    list_file = os.path.join(tmp, "concat.txt")
    with open(list_file, "w") as fh:
        for p in parts:
            fh.write(f"file '{p}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out])
    return out
