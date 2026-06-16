"""EDL (Edit Decision List) — the cut-list contract shared by render + FCPXML.

JSON uses ``in``/``out`` keys (matching video-use's ``edl.json``); the dataclass
field is ``in_`` because ``in`` is a Python keyword.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Segment:
    src: str
    in_: float
    out: float
    grade: Optional[str] = None
    overlays: Optional[list[dict[str, Any]]] = None

    def __post_init__(self) -> None:
        if self.out <= self.in_:
            raise ValueError(
                f"Segment out ({self.out}) must be greater than in ({self.in_})"
            )

    @property
    def duration(self) -> float:
        return self.out - self.in_

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"src": self.src, "in": self.in_, "out": self.out}
        if self.grade is not None:
            d["grade"] = self.grade
        if self.overlays is not None:
            d["overlays"] = self.overlays
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Segment":
        return cls(
            src=d["src"],
            in_=d["in"],
            out=d["out"],
            grade=d.get("grade"),
            overlays=d.get("overlays"),
        )


@dataclass
class EDL:
    fps: float
    resolution: tuple[int, int]
    segments: list[Segment]
    titles: list[dict[str, Any]] = field(default_factory=list)
    subtitles: bool = False
    music: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fps": self.fps,
            "resolution": list(self.resolution),
            "segments": [s.to_dict() for s in self.segments],
            "titles": self.titles,
            "subtitles": self.subtitles,
            "music": self.music,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EDL":
        if "segments" not in d or "fps" not in d or "resolution" not in d:
            raise ValueError("EDL requires 'fps', 'resolution', and 'segments'")
        return cls(
            fps=d["fps"],
            resolution=tuple(d["resolution"]),  # type: ignore[arg-type]
            segments=[Segment.from_dict(s) for s in d["segments"]],
            titles=d.get("titles", []),
            subtitles=d.get("subtitles", False),
            music=d.get("music"),
        )

    @classmethod
    def from_json(cls, s: str) -> "EDL":
        return cls.from_dict(json.loads(s))
