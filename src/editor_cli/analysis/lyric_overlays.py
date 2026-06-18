"""Group a word-level Transcript into timed caption phrases synced to the vocal.

Captions should appear *when the lyric is actually sung*, not on arbitrary cut
boundaries. This splits a word-level transcript into phrases at vocal gaps, drops a
leading intro region (producer tags / no lyric over the opening), and times each
phrase from its first sung word until the next phrase begins (the last phrase holds
briefly). Because the same audio is laid under the cut starting at 0, these vocal
timestamps map directly onto the reel timeline.

Works on any word objects exposing ``text``/``start``/``end`` (e.g. the Scribe
``Transcript.words``); pure logic, fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Phrase:
    text: str
    start: float
    end: float


def _is_word(w: Any) -> bool:
    return any(c.isalnum() for c in w.text)


def group_phrases(
    words: list[Any],
    *,
    gap: float = 0.34,
    intro_end: float = 0.0,
    tail_hold: float = 0.5,
    lead_in: float = 0.0,
) -> list[Phrase]:
    """Split ``words`` into vocal-synced phrases.

    - ``gap``: a silence longer than this starts a new phrase.
    - ``intro_end``: ignore words starting before this (opening tag / no lyric).
    - ``tail_hold``: how long the final phrase lingers past its last word.
    - ``lead_in``: show each phrase this many seconds before its first word (a small
      lead reads as "on the beat" rather than late).
    """
    lyr = [w for w in words if _is_word(w) and w.start >= intro_end]
    if not lyr:
        return []
    groups: list[list[Any]] = [[lyr[0]]]
    for w in lyr[1:]:
        if w.start - groups[-1][-1].end > gap:
            groups.append([w])
        else:
            groups[-1].append(w)

    phrases: list[Phrase] = []
    for i, g in enumerate(groups):
        text = " ".join(w.text.strip(",.!?") for w in g).strip()
        start = max(0.0, g[0].start - lead_in)
        end = groups[i + 1][0].start - lead_in if i + 1 < len(groups) else g[-1].end + tail_hold
        phrases.append(Phrase(text=text, start=round(start, 2), end=round(end, 2)))
    return phrases
