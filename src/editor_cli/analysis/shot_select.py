"""Shot-moment selection — pick the most engaging in-point for each EDL segment.

``reason_edl`` chooses each segment's window from the footage *manifest* and
*transcript* alone — it never looks at the pixels — so the in-point often lands
on a weak or transitional moment (motion blur, a subject mid-frame, an empty
beat). This stage *studies the footage*: for each segment it samples frames
evenly across the source clip, asks the vision model which frame is the strongest
on screen (sharp subject, peak action/expression, strong composition — not
blurry/transitional/empty), and re-centers the segment window on that moment
while preserving its duration.

All I/O is injected — frame sampling and the vision call — so the selection logic
is fully unit-testable offline. A per-clip failure falls back to the original
window (and is reported via ``on_skip``) rather than aborting the whole edit.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from editor_cli.domain.edl import EDL, Segment

# (src, n) -> [(timestamp_seconds, image_path), ...] in time order
FrameSampler = Callable[[str, int], list[tuple[float, str]]]
# (prompt, image_paths) -> raw model text
VisionFn = Callable[[str, list[str]], str]

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_RETRY_SUFFIX = '\n\nReturn ONLY JSON: {"index": int}. No prose, no fences.'

_PROMPT = (
    "You are selecting the single most engaging, freeze-worthy moment from ONE "
    "video clip for a fast-paced social reel. Below are {n} frames sampled evenly "
    "through the clip, in order, each labeled with its index and timestamp. Pick "
    "the index whose moment is strongest on screen: subject clearly visible and in "
    "focus, peak action / expression / motion, strong composition and lighting. "
    "AVOID frames that are motion-blurred, mid-transition, empty, half-occluded, or "
    'awkward. Return ONLY JSON: {{"index": int, "reason": "short"}}.\n\nFRAMES:\n{labels}'
)


def _parse_index(raw: str, n: int) -> int:
    match = _FENCE.search(raw)
    data = json.loads(match.group(1) if match else raw)
    idx = int(data["index"])
    if not 0 <= idx < n:
        raise ValueError(f"index {idx} out of range 0..{n - 1}")
    return idx


def best_moment(frames: list[tuple[float, str]], analyze: VisionFn) -> float:
    """Return the timestamp of the most engaging frame among ``frames``.

    One corrective retry is attempted on malformed/out-of-range output.
    """
    if not frames:
        raise ValueError("no frames to choose from")
    labels = "\n".join(f"[{i}] t={t:.2f}s" for i, (t, _) in enumerate(frames))
    prompt = _PROMPT.format(n=len(frames), labels=labels)
    paths = [p for _, p in frames]
    try:
        idx = _parse_index(analyze(prompt, paths), len(frames))
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        idx = _parse_index(analyze(prompt + _RETRY_SUFFIX, paths), len(frames))
    return frames[idx][0]


def recenter(duration: float, best_t: float, clip_dur: float) -> tuple[float, float]:
    """Window of ``duration`` centered on ``best_t``, clamped inside the clip."""
    new_in = best_t - duration / 2
    new_in = max(0.0, min(new_in, max(0.0, clip_dur - duration)))
    return new_in, new_in + duration


def refine_windows(
    edl: EDL,
    sample_frames: FrameSampler,
    analyze: VisionFn,
    durations: dict[str, float],
    *,
    num_samples: int = 10,
    on_skip: Optional[Callable[[str, Exception], None]] = None,
) -> EDL:
    """Return a new EDL with each segment re-centered on its clip's best moment.

    A clip whose sampling or vision call fails keeps its original window; the
    failure is surfaced to ``on_skip`` (if given) instead of being swallowed.
    """
    refined: list[Segment] = []
    for seg in edl.segments:
        try:
            frames = sample_frames(seg.src, num_samples)
            if not frames:
                raise ValueError(f"no frames sampled from {seg.src}")
            best_t = best_moment(frames, analyze)
            clip_dur = durations.get(seg.src) or frames[-1][0] or seg.out
            new_in, new_out = recenter(seg.duration, best_t, clip_dur)
            refined.append(Segment(seg.src, new_in, new_out, seg.grade, seg.overlays))
        except Exception as exc:  # noqa: BLE001 — documented per-clip fallback
            if on_skip is not None:
                on_skip(seg.src, exc)
            refined.append(seg)
    return EDL(
        fps=edl.fps,
        resolution=edl.resolution,
        segments=refined,
        titles=edl.titles,
        subtitles=edl.subtitles,
        music=edl.music,
    )
