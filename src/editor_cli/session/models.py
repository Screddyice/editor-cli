"""Immutable contracts shared by the Final Cut controller surfaces."""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal


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
        checks = tuple(item.strip() for item in self.required_operations)
        if len(set(checks)) != len(checks):
            raise ValueError("Required verification checks must be unique")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item) for item in checks):
            raise ValueError(
                "Each required verification check must be a lowercase identifier"
            )
        object.__setattr__(self, "required_operations", checks)


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


_ACTION_FIELDS: dict[tuple[str, str], dict[str, type | tuple[type, ...]]] = {
    ("edit", "insert_clip"): {
        "asset_id": str,
        "asset_name": str,
        "position": str,
        "duration": str,
        "in_point": str,
        "out_point": str,
        "ripple": bool,
    },
    ("edit", "delete_clips"): {"clip_ids": list, "ripple": bool},
    ("edit", "trim_clip"): {
        "clip_id": str,
        "trim_start": str,
        "trim_end": str,
        "ripple": bool,
    },
    ("edit", "split_clip"): {"clip_id": str, "split_points": list},
    ("edit", "reorder_clips"): {
        "clip_ids": list,
        "target_position": str,
        "ripple": bool,
    },
    ("edit", "change_speed"): {
        "clip_id": str,
        "speed": (int, float),
        "preserve_pitch": bool,
    },
    ("edit", "add_transition"): {
        "clip_id": str,
        "position": str,
        "transition_type": str,
        "duration": str,
    },
    ("edit", "add_audio"): {
        "parent_clip_id": str,
        "asset_id": str,
        "src": str,
        "offset": str,
        "duration": str,
        "role": str,
        "lane": int,
    },
    ("edit", "add_connected_clip"): {
        "parent_clip_id": str,
        "asset_id": str,
        "asset_name": str,
        "offset": str,
        "duration": str,
        "lane": int,
    },
    ("edit", "assign_role"): {
        "clip_id": str,
        "audio_role": str,
        "video_role": str,
    },
    ("edit", "fill_gaps"): {"mode": str, "max_gap": str},
    ("edit", "fix_flash_frames"): {"mode": str, "threshold_frames": int},
    ("edit", "remove_silence_candidates"): {
        "mode": str,
        "min_gap_seconds": (int, float),
        "min_confidence": (int, float),
    },
    ("mark", "add_marker"): {
        "timecode": str,
        "name": str,
        "marker_type": str,
        "note": str,
    },
    ("mark", "batch_add_markers"): {
        "markers": list,
        "auto_at_cuts": bool,
        "auto_at_intervals": str,
    },
    ("generate", "apply_template"): {
        "template_name": str,
        "clips": dict,
        "fps": (int, float),
    },
}

_REQUIRED_FIELDS = {
    ("edit", "insert_clip"): frozenset({"position"}),
    ("edit", "delete_clips"): frozenset({"clip_ids"}),
    ("edit", "trim_clip"): frozenset({"clip_id"}),
    ("edit", "split_clip"): frozenset({"clip_id", "split_points"}),
    ("edit", "reorder_clips"): frozenset({"clip_ids", "target_position"}),
    ("edit", "change_speed"): frozenset({"clip_id", "speed"}),
    ("edit", "add_transition"): frozenset({"clip_id"}),
    ("edit", "add_audio"): frozenset(),
    ("edit", "add_connected_clip"): frozenset({"parent_clip_id"}),
    ("edit", "assign_role"): frozenset({"clip_id"}),
    ("edit", "fill_gaps"): frozenset(),
    ("edit", "fix_flash_frames"): frozenset(),
    ("edit", "remove_silence_candidates"): frozenset(),
    ("mark", "add_marker"): frozenset({"timecode", "name"}),
    ("mark", "batch_add_markers"): frozenset(),
    ("generate", "apply_template"): frozenset({"template_name", "clips"}),
}

_ENUM_FIELDS = {
    (("edit", "add_transition"), "position"): {"start", "end", "both"},
    (("edit", "add_transition"), "transition_type"): {
        "cross-dissolve",
        "fade-to-black",
        "fade-from-black",
        "wipe",
    },
    (("edit", "fill_gaps"), "mode"): {
        "extend_previous",
        "extend_next",
        "delete",
    },
    (("edit", "fix_flash_frames"), "mode"): {
        "extend_previous",
        "extend_next",
        "delete",
        "auto",
    },
    (("edit", "remove_silence_candidates"), "mode"): {"delete", "mark"},
    (("mark", "add_marker"), "marker_type"): {
        "standard",
        "chapter",
        "todo",
        "completed",
    },
}


def _matches_type(value: Any, expected: type | tuple[type, ...]) -> bool:
    if isinstance(value, bool) and expected is not bool:
        return False
    return isinstance(value, expected)


def _require_string_list(value: Any, field: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"Edit argument {field} must be a non-empty list of strings")


def _validate_template_clips(
    clips: dict[str, Any],
    path_resolver: Callable[[Path], Path] | None,
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for slot, raw in clips.items():
        if not isinstance(slot, str) or not slot or not isinstance(raw, dict):
            raise ValueError("Template clips must map named slots to objects")
        unknown = set(raw) - {"src", "asset_id", "name", "duration"}
        if unknown:
            raise ValueError(
                f"Template clip {slot} has unknown arguments: {sorted(unknown)}"
            )
        if ("src" in raw) == ("asset_id" in raw):
            raise ValueError(
                f"Template clip {slot} needs exactly one of src or asset_id"
            )
        if any(not isinstance(value, str) or not value for value in raw.values()):
            raise ValueError(f"Template clip {slot} values must be non-empty strings")
        item = dict(raw)
        if "src" in item:
            if path_resolver is None:
                raise ValueError("Template clip src requires a session path authorizer")
            item["src"] = str(path_resolver(Path(item["src"])))
        normalized[slot] = item
    return normalized


def _validated_arguments(
    operation: EditOperation,
    path_resolver: Callable[[Path], Path] | None,
) -> dict[str, Any]:
    key = (operation.group, operation.action)
    fields = _ACTION_FIELDS[key]
    arguments = deepcopy(operation.arguments)
    if not isinstance(arguments, dict):
        raise TypeError(f"Edit arguments for {operation.action} must be an object")
    unknown = set(arguments) - set(fields)
    if unknown:
        raise ValueError(f"{operation.action} has unknown arguments: {sorted(unknown)}")
    missing = _REQUIRED_FIELDS[key] - set(arguments)
    if missing:
        raise ValueError(f"{operation.action} is missing arguments: {sorted(missing)}")
    for name, value in arguments.items():
        if not _matches_type(value, fields[name]):
            raise ValueError(f"Edit argument {name} has the wrong type")
        if isinstance(value, str) and not value:
            raise ValueError(f"Edit argument {name} cannot be empty")
        choices = _ENUM_FIELDS.get((key, name))
        if choices is not None and value not in choices:
            raise ValueError(f"Edit argument {name} has an unsupported value")

    for name in ("clip_ids", "split_points"):
        if name in arguments:
            _require_string_list(arguments[name], name)
    if key == ("mark", "batch_add_markers") and "markers" in arguments:
        markers = arguments["markers"]
        if not isinstance(markers, list):
            raise ValueError("markers must be a list")
        normalized_markers = []
        for marker in markers:
            if not isinstance(marker, dict):
                raise TypeError("Each marker must be an object")
            unknown_marker = set(marker) - {"timecode", "name", "marker_type", "note"}
            if unknown_marker:
                raise ValueError(
                    f"Marker has unknown arguments: {sorted(unknown_marker)}"
                )
            if not {"timecode", "name"}.issubset(marker):
                raise ValueError("Each marker needs timecode and name")
            if any(not isinstance(item, str) for item in marker.values()):
                raise ValueError("Marker values must be strings")
            normalized_markers.append(dict(marker))
        arguments["markers"] = normalized_markers
    if key == ("generate", "apply_template"):
        arguments["clips"] = _validate_template_clips(arguments["clips"], path_resolver)
    if key == ("edit", "add_audio") and "src" in arguments:
        if path_resolver is None:
            raise ValueError("add_audio src requires a session path authorizer")
        arguments["src"] = str(path_resolver(Path(arguments["src"])))
    if key == ("edit", "insert_clip") and not (
        arguments.get("asset_id") or arguments.get("asset_name")
    ):
        raise ValueError("insert_clip needs asset_id or asset_name")
    if key == ("edit", "add_audio") and not (
        arguments.get("asset_id") or arguments.get("src")
    ):
        raise ValueError("add_audio needs asset_id or src")
    if key == ("edit", "add_connected_clip") and not (
        arguments.get("asset_id") or arguments.get("asset_name")
    ):
        raise ValueError("add_connected_clip needs asset_id or asset_name")
    if key == ("edit", "assign_role") and not (
        arguments.get("audio_role") or arguments.get("video_role")
    ):
        raise ValueError("assign_role needs audio_role or video_role")
    return arguments


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
        self.validated_for(analysis)

    def validated_for(
        self,
        analysis: dict[str, Any],
        path_resolver: Callable[[Path], Path] | None = None,
    ) -> EditProgram:
        duration = float(analysis["duration_seconds"])
        for start, end in self.changed_ranges:
            if start < 0 or end <= start or end > duration:
                raise ValueError(
                    f"Changed range is outside the timeline: {start}-{end}"
                )
        operations = tuple(
            EditOperation(
                group=operation.group,
                action=operation.action,
                arguments=_validated_arguments(operation, path_resolver),
            )
            for operation in self.operations
        )
        return EditProgram(operations=operations, changed_ranges=self.changed_ranges)


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


@dataclass(frozen=True)
class EvidenceBinding:
    session_id: str
    pass_number: int
    state_version: int
    project_name: str
    candidate_sha256: str
    preview_sha256: str
    manifest_sha256: str
    frame_timestamps: tuple[float, ...]


@dataclass(frozen=True)
class ReviewReportInput:
    binding: EvidenceBinding
    required: dict[str, bool]
    observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalAction:
    token: str
    action: str
    arguments: dict[str, Any]
    expected: dict[str, Any]
    status: Literal["pending", "complete", "blocked"]
