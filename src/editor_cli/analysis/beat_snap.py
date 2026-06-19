"""Snap EDL cut boundaries onto musical beats so clips change *on the beat*.

reason_edl picks shot durations for pacing/story, not for the music, so cuts drift
off the beat and the edit feels like it doesn't flow. Given the track's beat times,
this nudges each internal cut to its nearest beat (keeping the opening and the final
end fixed), bumping to the next beat if two cuts would collide so the cut count is
preserved. Pure + unit-testable; the beat times come from a detector (librosa) at
the call site.
"""

from __future__ import annotations


def quantize_to_beats(
    durations: list[float], beats: list[float], *, end: float | None = None
) -> list[float]:
    """Lay shots on a beat grid: each duration becomes a whole number of beats and
    every cut lands on an exact beat time.

    Returns the cut boundaries ``[0, t1, ..., end]``. Each shot consumes
    ``round(dur / median_beat_period)`` beats (>=1); the cursor walks the beat list
    so cuts sit on real beats even when the grid isn't perfectly even. The final
    boundary is ``end`` (e.g. the track length) so the last shot runs to the music.
    Snapping irregular durations to beat *multiples* gives a cleaner on-beat cadence
    than nudging each arbitrary cut to its nearest beat.
    """
    if not beats or not durations:
        return [0.0, end if end is not None else sum(durations)]
    beats = sorted(beats)
    diffs = [b - a for a, b in zip(beats, beats[1:])] or [beats[0] or 1.0]
    period = sorted(diffs)[len(diffs) // 2]
    out, idx = [0.0], 0
    for i, d in enumerate(durations):
        idx = min(idx + max(1, round(d / period)), len(beats) - 1)
        t = end if (end is not None and i == len(durations) - 1) else beats[idx]
        if t > out[-1] + 1e-6:
            out.append(round(t, 3))
    return out


def snap_cuts(boundaries: list[float], beats: list[float]) -> list[float]:
    """Return ``boundaries`` with each internal cut moved to its nearest beat.

    ``boundaries`` is the cumulative cut timeline ``[0, t1, t2, ..., end]`` (N cuts
    => N+1 boundaries). The first and last are preserved (clip start / music tail);
    every interior cut snaps to the closest beat, and if that would land on or before
    the previous cut it is bumped to the next beat so all cuts survive in order.
    """
    if len(boundaries) < 3 or not beats:
        return list(boundaries)
    beats = sorted(beats)
    out = [boundaries[0]]
    for t in boundaries[1:-1]:
        s = min(beats, key=lambda b: abs(b - t))
        if s <= out[-1]:
            later = [b for b in beats if b > out[-1]]
            s = later[0] if later else out[-1]
        out.append(s)
    out.append(boundaries[-1])
    return out
