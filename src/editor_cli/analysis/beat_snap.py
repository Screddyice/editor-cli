"""Snap EDL cut boundaries onto musical beats so clips change *on the beat*.

reason_edl picks shot durations for pacing/story, not for the music, so cuts drift
off the beat and the edit feels like it doesn't flow. Given the track's beat times,
this nudges each internal cut to its nearest beat (keeping the opening and the final
end fixed), bumping to the next beat if two cuts would collide so the cut count is
preserved. Pure + unit-testable; the beat times come from a detector (librosa) at
the call site.
"""

from __future__ import annotations


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
