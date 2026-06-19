"""Arms-length bridge to the bundled OpenMontage / HyperFrames overlay engine.

OpenMontage (``vendor/OpenMontage`` submodule) is **AGPL-3.0**; editor-cli is
MIT. To keep that boundary, this module **only ever shells out** to the engine
as a separate process (``npx hyperframes ...``) — it never imports OpenMontage
Python. Crossing to an ``import`` would pull AGPL copyleft onto editor-cli, so
don't. The overlays HyperFrames renders (animated titles, lower-thirds,
audio-reactive captions) are composited onto footage with ffmpeg by
``editor_cli.render.ffmpeg.overlay_onto`` — also our own MIT code.

Live rendering needs Node >= 22 + ffmpeg on PATH; use ``runtime_status()`` to
check before relying on it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Sequence

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class OverlayError(RuntimeError):
    pass


def _default_runner(cmd: Sequence[str], **kw) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(cmd), capture_output=True, text=True, **kw)


def submodule_root() -> Path:
    """Locate the vendored OpenMontage submodule.

    Honours ``EDITOR_CLI_OPENMONTAGE_ROOT``; otherwise resolves ``vendor/
    OpenMontage`` from the repo (dev/source layout), falling back to the cwd.
    Raises if it isn't checked out (``git submodule update --init``).
    """
    env = os.environ.get("EDITOR_CLI_OPENMONTAGE_ROOT")
    candidates = [Path(env)] if env else []
    candidates.append(Path(__file__).resolve().parents[3] / "vendor" / "OpenMontage")
    candidates.append(Path.cwd() / "vendor" / "OpenMontage")
    for c in candidates:
        if c.is_dir() and any(c.iterdir()):
            return c
    raise OverlayError(
        "OpenMontage submodule not found — run `git submodule update --init` "
        "(or set EDITOR_CLI_OPENMONTAGE_ROOT)."
    )


def _probe(cmd: Sequence[str], runner: Runner) -> str | None:
    try:
        res = runner(cmd)
    except (OSError, ValueError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def runtime_status(runner: Runner = _default_runner) -> dict:
    """Doctor: report whether the HyperFrames runtime floor is satisfied.

    Returns {node, ffmpeg, npx, hyperframes, ok}. ``ok`` is True only when node,
    ffmpeg and the hyperframes CLI all resolve.
    """
    node = _probe(["node", "--version"], runner)
    ffmpeg = _probe(["ffmpeg", "-version"], runner) is not None
    npx = _probe(["npx", "--version"], runner) is not None
    hyperframes = _probe(["npx", "--no-install", "hyperframes", "--version"], runner)
    return {
        "node": node,
        "ffmpeg": ffmpeg,
        "npx": npx,
        "hyperframes": hyperframes,
        "ok": bool(node and ffmpeg and hyperframes),
    }


def scaffold(
    project_dir: str | Path,
    *,
    template: str = "blank",
    video: str | None = None,
    runner: Runner = _default_runner,
) -> Path:
    """Scaffold a HyperFrames composition workspace (``hyperframes init``).

    Returns the project dir. The composition HTML is then authored there before
    ``render_overlay``.
    """
    project_dir = Path(project_dir)
    cmd = ["npx", "hyperframes", "init", str(project_dir),
           "--example", template, "--non-interactive"]
    if video:
        cmd += ["--video", str(video)]
    res = runner(cmd)
    if res.returncode != 0:
        raise OverlayError(f"hyperframes init failed: {res.stderr[-800:]}")
    return project_dir


def render_overlay(
    project_dir: str | Path,
    *,
    strict: bool = False,
    extra_args: Sequence[str] = (),
    runner: Runner = _default_runner,
) -> str:
    """Render a scaffolded HyperFrames project to video via ``hyperframes render``.

    Runs as a subprocess inside ``project_dir`` (process boundary = MIT-safe).
    Returns the project dir; HyperFrames writes the rendered file under it per
    its own convention. Raises OverlayError if the runtime is missing or the
    render fails.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise OverlayError(f"project dir not found: {project_dir}")
    if not runtime_status(runner)["hyperframes"]:
        raise OverlayError(
            "HyperFrames runtime unavailable — need Node >= 22 and the "
            "hyperframes CLI (npx hyperframes). Run `editor-cli motion-doctor`."
        )
    cmd = ["npx", "hyperframes", "render", *(["--strict"] if strict else []), *extra_args]
    res = runner(cmd, cwd=str(project_dir))
    if res.returncode != 0:
        raise OverlayError(f"hyperframes render failed: {res.stderr[-800:]}")
    return str(project_dir)
