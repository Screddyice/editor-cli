"""Title rendering dispatcher.

Picks how the editor's titles get onto the render:

- ``pillow``      — portable Pillow PNG + ffmpeg overlay (works everywhere).
- ``hyperframes`` — rich animated overlays via the bundled HyperFrames engine
                    (transparent webm) composited with ffmpeg.
- ``auto``        — HyperFrames when its runtime is ready, else Pillow.

The whole thing is driven by the edit (EDL titles), never a manual step.
"""

from __future__ import annotations

import os
import tempfile

from editor_cli.render import ffmpeg, hyperframes, overlays


def render(
    video: str,
    titles: list[dict],
    out: str,
    preview: bool = False,
    *,
    engine: str = "auto",
    runner=None,
) -> str:
    if engine == "pillow":
        return ffmpeg.apply_titles(video, titles, out, preview=preview)

    want_hf = engine == "hyperframes" or (
        engine == "auto" and overlays.runtime_status(runner or overlays._default_runner)["ok"]
    )
    if not want_hf:
        return ffmpeg.apply_titles(video, titles, out, preview=preview)

    w, h = ffmpeg._stream_dims(video)
    dur = ffmpeg.duration_of(video)
    webm = os.path.join(tempfile.mkdtemp(prefix="editor_cli_titles_"), "titles.webm")
    try:
        if hyperframes.render_titles_overlay(titles, w, h, dur, webm, runner=runner) is None:
            # nothing drawable -> pass through untouched
            return ffmpeg.apply_titles(video, titles, out, preview=preview)
        return ffmpeg.overlay_onto(video, webm, out, preview=preview)
    except overlays.OverlayError:
        if engine == "hyperframes":
            raise  # explicit request: surface the failure
        return ffmpeg.apply_titles(video, titles, out, preview=preview)  # auto degrades
