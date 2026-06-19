"""Author + render animated title overlays via the bundled HyperFrames engine.

Arms-length: this composes a HyperFrames HTML composition and drives
``npx hyperframes`` through :mod:`editor_cli.render.overlays` (subprocess only —
it never imports OpenMontage, so editor-cli stays MIT). It renders a
**transparent webm** containing only the animated titles; the ffmpeg compositor
(:func:`editor_cli.render.ffmpeg.overlay_onto`) lays that onto the footage.

Live rendering needs the HyperFrames runtime (Node >= 22 + the hyperframes CLI);
the title pipeline falls back to the portable Pillow path when it's unavailable.
"""

from __future__ import annotations

import html
import os
import tempfile
from typing import Sequence

from editor_cli.render import overlays, title_style
from editor_cli.render.ffmpeg import _title_layout

# absolute-positioned bands within the composition frame
_REGION_CSS = {
    "top": "top:8%;left:0;right:0;",
    "center": "top:0;bottom:0;left:0;right:0;display:flex;align-items:center;justify-content:center;",
    "bottom": "bottom:10%;left:0;right:0;",
}


def author_titles_html(layouts: Sequence[dict], w: int, h: int, duration: float) -> str:
    """Build a transparent HyperFrames composition (one animated clip per title).

    Pure function — returns the composition HTML. Each title is a ``class="clip"``
    element with ``data-start``/``data-duration``/``data-track-index`` and a GSAP
    fade+rise in/out registered on ``window.__timelines``.
    """
    clips: list[str] = []
    anims: list[str] = []
    fontsize = max(18, round(h / 16))
    for i, lay in enumerate(layouts):
        start, end = lay["start"], lay["end"]
        dur = end - start
        style = title_style.resolve(lay["style"])
        text = lay["text"].upper() if style.uppercase else lay["text"]
        clips.append(
            f'<div class="clip title" id="t{i}" '
            f'data-start="{start:.3g}" data-duration="{dur:.3g}" data-track-index="{i}" '
            f'style="position:absolute;{_REGION_CSS[lay["region"]]}'
            f"text-align:center;color:#fff;"
            f"font-family:'{style.family}',Helvetica,Arial,sans-serif;font-weight:800;"
            f"font-size:{fontsize}px;letter-spacing:0.02em;"
            f'text-shadow:0 2px 10px rgba(0,0,0,.65);opacity:0;">'
            f"<span>{html.escape(text)}</span></div>"
        )
        f = min(0.4, dur / 3.0)
        anims.append(
            f'tl.fromTo("#t{i}",{{opacity:0,y:24}},'
            f'{{opacity:1,y:0,duration:{f:.3g},ease:"power2.out"}},{start:.3g});'
            f'tl.to("#t{i}",{{opacity:0,duration:{f:.3g},ease:"power2.in"}},{end - f:.3g});'
        )
    nl = "\n"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<style>html,body{margin:0;background:transparent}*{box-sizing:border-box}</style>"
        "</head><body>"
        f'<div id="root" data-composition-id="titles" data-start="0" '
        f'data-duration="{duration:.3g}" data-width="{w}" data-height="{h}" '
        f'style="position:relative;width:{w}px;height:{h}px;background:transparent">'
        f"{nl.join(clips)}"
        "<script>"
        "const tl = gsap.timeline({ paused: true });"
        f"{nl.join(anims)}"
        'window.__timelines["titles"] = tl;'
        "</script>"
        "</div></body></html>"
    )


def render_titles_overlay(
    titles: list[dict],
    w: int,
    h: int,
    duration: float,
    out: str,
    *,
    runner=None,
) -> str | None:
    """Render the EDL's titles to a transparent webm via HyperFrames.

    Returns ``out`` (the transparent overlay) or ``None`` if no title has text.
    Raises :class:`overlays.OverlayError` if the runtime is unavailable. The
    caller composites ``out`` onto the footage with ``ffmpeg.overlay_onto``.
    """
    layouts = [lay for t in (titles or []) if (lay := _title_layout(t)) is not None]
    if not layouts:
        return None
    run = runner or overlays._default_runner
    if not overlays.runtime_status(run)["ok"]:
        raise overlays.OverlayError(
            "HyperFrames runtime unavailable — run `editor-cli motion-doctor` "
            "(needs Node >= 22 + the hyperframes CLI; warm with `npx hyperframes --version`)."
        )
    project = tempfile.mkdtemp(prefix="editor_cli_hf_")
    with open(os.path.join(project, "index.html"), "w") as fh:
        fh.write(author_titles_html(layouts, w, h, duration))
    overlays.render_overlay(
        project,
        extra_args=["--format", "webm", "--output", os.path.abspath(out)],
        runner=run,
    )
    return out
