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


def sample_frames(src: str, n: int, out_dir: str) -> list[tuple[float, str]]:
    """Extract ``n`` JPEG frames sampled evenly across ``src``.

    Returns ``[(timestamp_seconds, image_path), ...]`` in time order — the input
    the shot-moment selector needs to choose the most engaging in-point. Samples
    span the inner 5–95% of the clip so we never land on a black lead frame or a
    trailing fade.
    """
    if n < 1:
        return []
    dur = duration_of(src)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    span = dur * 0.90
    frames: list[tuple[float, str]] = []
    for i in range(n):
        t = dur * 0.05 + (span * i / (n - 1) if n > 1 else span / 2)
        img = os.path.join(out_dir, f"{stem}_{i:02d}.jpg")
        _run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", src,
              "-frames:v", "1", "-q:v", "3", img])
        frames.append((t, img))
    return frames


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
