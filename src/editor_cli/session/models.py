"""Immutable contracts shared by the Final Cut controller surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    IDLE = "idle"
    CAPTURE = "capture"
    PRESERVE = "preserve"
    ANALYZE = "analyze"
    APPLY = "apply"
    IMPORT = "import"
    PREVIEW = "preview"
    VERIFY = "verify"
    CORRECT = "correct"
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class EditRequest:
    prompt: str
    required_operations: tuple[str, ...] = ()
    internet_media: bool = True

    def __post_init__(self) -> None:
        clean = self.prompt.strip()
        if not clean:
            raise ValueError("Edit prompt cannot be empty")
        object.__setattr__(self, "prompt", clean)


ALLOWED_EDIT_ACTIONS = frozenset(
    {
        ("edit", "insert_clip"),
        ("edit", "delete_clips"),
        ("edit", "trim_clip"),
        ("edit", "split_clip"),
        ("edit", "reorder_clips"),
        ("edit", "change_speed"),
        ("edit", "add_transition"),
        ("edit", "add_audio"),
        ("edit", "add_connected_clip"),
        ("edit", "assign_role"),
        ("edit", "fill_gaps"),
        ("edit", "fix_flash_frames"),
        ("edit", "remove_silence_candidates"),
        ("mark", "add_marker"),
        ("mark", "batch_add_markers"),
        ("generate", "apply_template"),
    }
)


@dataclass(frozen=True)
class EditOperation:
    group: str
    action: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class EditProgram:
    operations: tuple[EditOperation, ...]
    changed_ranges: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.operations:
            raise ValueError("Edit program must contain at least one operation")
        for operation in self.operations:
            if (operation.group, operation.action) not in ALLOWED_EDIT_ACTIONS:
                raise ValueError(
                    f"Unsupported edit action: {operation.group}.{operation.action}"
                )

    def validate_for(self, analysis: dict[str, Any]) -> None:
        duration = float(analysis["duration_seconds"])
        for start, end in self.changed_ranges:
            if start < 0 or end <= start or end > duration:
                raise ValueError(
                    f"Changed range is outside the timeline: {start}-{end}"
                )


@dataclass(frozen=True)
class ProjectIdentity:
    library: str
    event: str
    project: str
    duration_seconds: float


@dataclass(frozen=True)
class PassResult:
    number: int
    fcpxml_path: str
    preview_path: str | None
    required_checks: dict[str, bool]
    score: float

    @property
    def verified(self) -> bool:
        return bool(self.required_checks) and all(self.required_checks.values())
