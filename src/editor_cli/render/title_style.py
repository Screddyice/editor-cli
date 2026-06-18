"""Caption/title styling — ONE typeface, two weights, applied consistently.

The reference-style analysis tags each on-screen line with a logical emphasis:
a punchy *impact* callout vs. a softer *accent* line. Rendering those as two
*unrelated* fonts (e.g. a heavy condensed face beside a formal script) reads as
incoherent — the text appears to "bounce" between fonts that don't belong
together. This module is the single source of truth for caption type: it maps an
emphasis to a concrete face drawn from ONE family in TWO weights, so captions can
vary in emphasis without ever clashing.

Family: **Avenir Next** (ships with macOS as a ``.ttc`` collection; face indices
are stable within the collection). Impact = Heavy, uppercase. Accent = Medium
Italic, mixed case. Both share the family, so the reel reads as one type system.

Pure data + mapping — no Pillow/ffmpeg import — so it stays dependency-free and
unit-testable, and both the FCPXML path and the ffmpeg overlay path resolve type
through the same authority.
"""

from __future__ import annotations

from dataclasses import dataclass

FONT_FAMILY = "Avenir Next"
FONT_FILE = "/System/Library/Fonts/Avenir Next.ttc"

# Substrings that mark a label as the *accent* (softer/secondary) treatment.
# Everything else — including bold/headline/uppercase callouts — is *impact*.
_ACCENT_HINTS = ("accent", "cursive", "script", "light", "secondary", "soft", "italic")


@dataclass(frozen=True)
class TitleStyle:
    """A concrete, renderable caption treatment from the shared family."""

    emphasis: str          # "impact" | "accent"
    family: str
    weight: str            # human face name, e.g. "Heavy" / "Medium Italic"
    font_file: str
    face_index: int        # index within the .ttc collection
    uppercase: bool


IMPACT = TitleStyle(
    emphasis="impact", family=FONT_FAMILY, weight="Heavy",
    font_file=FONT_FILE, face_index=8, uppercase=True,
)
ACCENT = TitleStyle(
    emphasis="accent", family=FONT_FAMILY, weight="Medium Italic",
    font_file=FONT_FILE, face_index=6, uppercase=False,
)


def classify(label: str | None) -> str:
    """Collapse an arbitrary source style label to ``"impact"`` or ``"accent"``."""
    key = (label or "").strip().lower()
    return "accent" if any(h in key for h in _ACCENT_HINTS) else "impact"


def resolve(label: str | None) -> TitleStyle:
    """Map any source style label to a concrete :class:`TitleStyle`.

    Unknown / empty labels default to :data:`IMPACT` — the safe, legible choice.
    """
    return ACCENT if classify(label) == "accent" else IMPACT
