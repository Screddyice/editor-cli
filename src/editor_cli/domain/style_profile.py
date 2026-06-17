"""StyleProfile — structured editing style extracted by Gemini from reference
videos, and the target the cut-reasoning + eval stages aim at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

_REQUIRED = ("pacing", "transitions", "automations", "color", "captions", "sound", "vibe")


@dataclass
class Pacing:
    cuts_per_min: float
    avg_shot_len_s: float

    def to_dict(self) -> dict[str, Any]:
        return {"cuts_per_min": self.cuts_per_min, "avg_shot_len_s": self.avg_shot_len_s}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Pacing":
        return cls(cuts_per_min=d["cuts_per_min"], avg_shot_len_s=d["avg_shot_len_s"])


@dataclass
class Color:
    description: str
    lut: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"description": self.description, "lut": self.lut}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Color":
        return cls(description=d["description"], lut=d.get("lut"))


@dataclass
class Captions:
    style: str
    position: str
    font: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"style": self.style, "position": self.position, "font": self.font}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Captions":
        return cls(style=d["style"], position=d["position"], font=d.get("font"))


@dataclass
class Sound:
    name: Optional[str]
    energy: str
    genre: str
    bpm: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "energy": self.energy, "genre": self.genre, "bpm": self.bpm}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Sound":
        return cls(name=d.get("name"), energy=d["energy"], genre=d["genre"], bpm=d.get("bpm"))


@dataclass
class StyleProfile:
    pacing: Pacing
    transitions: list[str]
    automations: list[str]
    color: Color
    captions: Captions
    sound: Sound
    vibe: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pacing": self.pacing.to_dict(),
            "transitions": self.transitions,
            "automations": self.automations,
            "color": self.color.to_dict(),
            "captions": self.captions.to_dict(),
            "sound": self.sound.to_dict(),
            "vibe": self.vibe,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StyleProfile":
        missing = [k for k in _REQUIRED if k not in d]
        if missing:
            raise ValueError(f"StyleProfile missing required keys: {missing}")
        return cls(
            pacing=Pacing.from_dict(d["pacing"]),
            transitions=list(d["transitions"]),
            automations=list(d["automations"]),
            color=Color.from_dict(d["color"]),
            captions=Captions.from_dict(d["captions"]),
            sound=Sound.from_dict(d["sound"]),
            vibe=d["vibe"],
        )

    @classmethod
    def from_json(cls, s: str) -> "StyleProfile":
        return cls.from_dict(json.loads(s))
