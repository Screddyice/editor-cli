"""Strict review contracts for technical and agent visual evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewReport:
    required: dict[str, bool]
    observations: tuple[str, ...]
    changed_ranges: tuple[tuple[float, float], ...] = ()

    @property
    def verified(self) -> bool:
        return bool(self.required) and all(self.required.values())

    @property
    def score(self) -> float:
        if not self.required:
            return 0.0
        return sum(self.required.values()) / len(self.required)


def parse_creative_review(
    raw: str,
    required_keys: tuple[str, ...],
) -> ReviewReport:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Creative review must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Creative review JSON must be an object")

    required = value.get("required")
    if not isinstance(required, dict):
        raise ValueError("Creative review requires a 'required' object")
    expected = set(required_keys)
    actual = set(required)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ValueError("Creative review check mismatch: " + "; ".join(details))
    if any(type(result) is not bool for result in required.values()):
        raise ValueError("Creative review check results must be booleans")

    observations = value.get("observations", [])
    if not isinstance(observations, list) or any(
        not isinstance(item, str) for item in observations
    ):
        raise ValueError("Creative review observations must be a list of strings")

    raw_ranges = value.get("changed_ranges", [])
    if not isinstance(raw_ranges, list):
        raise ValueError("Creative review changed_ranges must be a list")
    changed_ranges: list[tuple[float, float]] = []
    for item in raw_ranges:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("Each creative review range needs start and end")
        start, end = float(item[0]), float(item[1])
        if start < 0 or end <= start:
            raise ValueError(f"Invalid creative review range: {start}-{end}")
        changed_ranges.append((start, end))

    return ReviewReport(
        required=dict(required),
        observations=tuple(observations),
        changed_ranges=tuple(changed_ranges),
    )


def combine_reports(
    technical: ReviewReport, creative: ReviewReport
) -> ReviewReport:
    overlap = technical.required.keys() & creative.required.keys()
    if overlap:
        raise ValueError(f"Duplicate verification keys: {sorted(overlap)}")
    return ReviewReport(
        required={**technical.required, **creative.required},
        observations=technical.observations + creative.observations,
        changed_ranges=creative.changed_ranges,
    )
