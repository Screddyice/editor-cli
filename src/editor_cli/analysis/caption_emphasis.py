"""Per-word caption emphasis — which words get the script, which stay grotesque.

The reference reel doesn't switch fonts per *line*, it switches per *word*: a
functional lead-in stays in the grotesque ("my", "i just") while the emotional /
aesthetic payoff word gets the script ("Wrist", "Laughed"). Whole declarative
setup lines — the all-caps hook — stay grotesque throughout. That call is editorial
*valence*, not grammar (it's why a lexicon can't capture it: "locked" stays plain in
the hook but "Laughed" goes script), so it is delegated to the model. This module
owns the prompt, parsing, validation, and a safe fallback: if the model is
unavailable or its tag count doesn't line up with the words, the whole line falls
back to its overlay-level emphasis — surfaced via ``on_error``, never silently.

The generate fn is injected (``(prompt) -> str``) so the logic is unit-testable
offline; the real binding is a text-only Gemini call.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from editor_cli.render.title_style import classify

TextGenerateFn = Callable[[str], str]

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_PROMPT = (
    "Tag each word of a short-form reel caption as either 'script' (the emotional, "
    "aesthetic, or aspirational PAYOFF word — a key noun or release verb an editor "
    "would beautify) or 'plain' (functional, grounded, or part of a declarative hook "
    "line). Whole all-caps hook lines stay all 'plain'. Examples:\n"
    "'POV: YOU LOCKED IN' -> all plain\n"
    "'my Wrist' -> my=plain, Wrist=script\n"
    "'i just Laughed' -> i=plain, just=plain, Laughed=script\n"
    "'Run Run Run' -> all script\n'Heart' -> script\n\"I got M's\" -> all plain\n"
    'Return ONLY JSON: {{"tags": ["plain"|"script", ...]}} — one per word, in order, '
    "for this caption:\n{line}"
)


def _emphasis(tag: object) -> str:
    return "accent" if str(tag).strip().lower().startswith("s") else "declarative"


def tag_words(
    text: str,
    generate: TextGenerateFn,
    *,
    overlay_style: str | None = None,
    on_error: Optional[Callable[[str, Exception], None]] = None,
) -> list[tuple[str, str]]:
    """Return ``[(word, emphasis), ...]`` where emphasis is ``"declarative"`` or
    ``"accent"``. On any model/parse failure the whole line falls back to its
    overlay-level emphasis (``classify(overlay_style)``)."""
    words = text.split()
    if not words:
        return []
    fallback = classify(overlay_style)
    try:
        raw = generate(_PROMPT.format(line=text))
        match = _FENCE.search(raw)
        tags = json.loads(match.group(1) if match else raw)["tags"]
        if len(tags) != len(words):
            raise ValueError(f"{len(tags)} tags for {len(words)} words")
        return [(w, _emphasis(t)) for w, t in zip(words, tags)]
    except Exception as exc:  # noqa: BLE001 — documented whole-line fallback
        if on_error is not None:
            on_error(text, exc)
        return [(w, fallback) for w in words]


def runs(tagged: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
    """Collapse per-word tags into consecutive same-emphasis runs, preserving
    order: ``[("declarative", ["i","just"]), ("accent", ["Laughed"])]``."""
    out: list[tuple[str, list[str]]] = []
    for word, emph in tagged:
        if out and out[-1][0] == emph:
            out[-1][1].append(word)
        else:
            out.append((emph, [word]))
    return out
