"""Caption/title typography — replicate the reference reel's TWO-font language.

Studying the reference reel frame-by-frame shows a deliberate two-typeface system,
and the switch between them carries meaning:

- **declarative** — the grounded, functional, "spoken" words ("POV: YOU LOCKED IN",
  "i just", "push", "is hard"): a clean **condensed** neo-grotesque, **Helvetica Neue
  Condensed Bold**, lightly tracked, case preserved (the hook reads as tracked caps).
  The narrow proportions give it character — a wide regular grotesque reads generic.
- **accent** — the emotional / aesthetic *payoff* words ("Heart", "Art", "Calm",
  "Run", "Laughed"): a formal English roundhand, **Snell Roundhand Bold**, larger,
  with the reference's faint white→ice-blue vertical sheen.

Both treatments are clean white with a SOFT drop shadow for legibility — the
reference uses **no hard outline**; a thick black stroke reads as cartoonish, which
is the look to avoid. The renderer owns the shadow; this module owns font identity.

Why two fonts and not one family (the v4 mistake): the reference's whole device is
the *tension* between a utilitarian grotesque and a luxurious script — hustle vs.
soul, the grind vs. the beautiful life it buys. The script is reserved for the word
that matters emotionally; everything else stays plain. The two HARMONIZE because both
are refined — the failure mode (v3) was pairing the elegant script with a crude
display face (Impact), which reads as clashing rather than intentional.

So: match the reference exactly where we can (these are its actual faces, or the
closest macOS equivalents) and, above all, keep the same stylistic language — switch
to the script *on the emotional word*, never font-bounce arbitrarily.

Pure data + mapping (no Pillow/ffmpeg import) so it stays dependency-free and
unit-testable; the FCPXML path and the ffmpeg overlay path resolve type through this
one authority.
"""

from __future__ import annotations

from dataclasses import dataclass

# Declarative: the reference sans is CONDENSED, not the wide default — a regular
# Helvetica reads generic against the cinematic frames. Condensed Bold matches its
# narrow proportions while staying clean (not the heavy/cartoonish Condensed Black).
SANS_FAMILY = "Helvetica Neue"
SANS_FILE = "/System/Library/Fonts/HelveticaNeue.ttc"
SANS_INDEX = 4  # Condensed Bold

# Accent: formal English roundhand. The reference sets it at a refined Bold weight
# with a SOFT shadow (no hard outline) — Black reads too heavy / cartoonish.
SCRIPT_FAMILY = "Snell Roundhand"
SCRIPT_FILE = "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc"
SCRIPT_INDEX = 1  # Bold

# Substrings marking a label as the *accent* (script) treatment; everything else
# is declarative. "cursive"/"script" are how the reference-style analysis tags the
# emotional payoff lines.
_ACCENT_HINTS = ("accent", "cursive", "script", "italic", "soft")


@dataclass(frozen=True)
class TitleStyle:
    """A concrete, renderable caption treatment."""

    emphasis: str          # "declarative" | "accent"
    family: str
    font_file: str
    face_index: int
    uppercase: bool        # force upper? (no — the reference preserves source case)
    tracking: float        # extra letter-spacing as a fraction of font size
    gradient: bool         # apply the reference's white→ice-blue vertical sheen


DECLARATIVE = TitleStyle(
    emphasis="declarative", family=SANS_FAMILY, font_file=SANS_FILE,
    face_index=SANS_INDEX, uppercase=False, tracking=0.07, gradient=False,
)
ACCENT = TitleStyle(
    emphasis="accent", family=SCRIPT_FAMILY, font_file=SCRIPT_FILE,
    face_index=SCRIPT_INDEX, uppercase=False, tracking=0.0, gradient=True,
)


def classify(label: str | None) -> str:
    """Collapse an arbitrary source style label to ``"declarative"`` or ``"accent"``."""
    key = (label or "").strip().lower()
    return "accent" if any(h in key for h in _ACCENT_HINTS) else "declarative"


def resolve(label: str | None) -> TitleStyle:
    """Map any source style label to a concrete :class:`TitleStyle`.

    Unknown / empty labels default to :data:`DECLARATIVE` — the plain, legible
    treatment; the script is only ever applied to explicitly emotional lines.
    """
    return ACCENT if classify(label) == "accent" else DECLARATIVE
