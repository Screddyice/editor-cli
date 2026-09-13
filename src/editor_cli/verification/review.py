"""Strict review contracts for technical and agent visual evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass

from editor_cli.session.models import EvidenceBinding, FrozenDict


@dataclass(frozen=True)
class ReviewReport:
    required: dict[str, bool]
    observations: tuple[str, ...]
    changed_ranges: tuple[tuple[float, float], ...] = ()
    binding: EvidenceBinding | None = None

    def __post_init__(self) -> None:
        if any(type(result) is not bool for result in self.required.values()):
            raise ValueError("Review check results must be booleans")
        object.__setattr__(self, "required", FrozenDict(self.required))
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "changed_ranges", tuple(self.changed_ranges))

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
    *,
    expected_binding: EvidenceBinding | None = None,
) -> ReviewReport:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Creative review must be valid JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("Creative review JSON must be an object")
    allowed_fields = {"required", "observations", "changed_ranges", "binding"}
    extra_fields = set(value) - allowed_fields
    if extra_fields:
        raise ValueError(
            f"Creative review has unexpected fields: {sorted(extra_fields)}"
        )

    required = value.get("required")
    if not isinstance(required, dict):
        raise TypeError("Creative review requires a 'required' object")
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
        raise ValueError(
            "Creative review must contain the exact required checks: "
            + "; ".join(details)
        )
    if any(type(result) is not bool for result in required.values()):
        raise ValueError("Creative review check results must be booleans")

    observations = value.get("observations", [])
    if not isinstance(observations, list) or any(
        not isinstance(item, str) for item in observations
    ):
        raise ValueError("Creative review observations must be a list of strings")

    raw_ranges = value.get("changed_ranges", [])
    if not isinstance(raw_ranges, list):
        raise TypeError("Creative review changed_ranges must be a list")
    changed_ranges: list[tuple[float, float]] = []
    for item in raw_ranges:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("Each creative review range needs start and end")
        start, end = float(item[0]), float(item[1])
        if start < 0 or end <= start:
            raise ValueError(f"Invalid creative review range: {start}-{end}")
        changed_ranges.append((start, end))

    binding: EvidenceBinding | None = None
    raw_binding = value.get("binding")
    if expected_binding is not None or raw_binding is not None:
        try:
            binding = EvidenceBinding.from_dict(raw_binding)
        except (TypeError, ValueError) as exc:
            raise ValueError("Creative review binding is invalid") from exc
    if expected_binding is not None and binding is not None:
        if binding.preview_sha256 != expected_binding.preview_sha256:
            raise ValueError(
                "Creative review preview hash does not match the candidate"
            )
        if binding.candidate_sha256 != expected_binding.candidate_sha256:
            raise ValueError(
                "Creative review candidate hash does not match the candidate"
            )
        if binding.manifest_sha256 != expected_binding.manifest_sha256:
            raise ValueError(
                "Creative review manifest hash does not match the evidence"
            )
        if binding.state_version != expected_binding.state_version:
            raise ValueError("Creative review state version is stale")
        if binding != expected_binding:
            raise ValueError("Creative review binding does not match the candidate")

    return ReviewReport(
        required=dict(required),
        observations=tuple(observations),
        changed_ranges=tuple(changed_ranges),
        binding=binding,
    )


def combine_reports(technical: ReviewReport, creative: ReviewReport) -> ReviewReport:
    overlap = technical.required.keys() & creative.required.keys()
    if overlap:
        raise ValueError(f"Duplicate verification keys: {sorted(overlap)}")
    return ReviewReport(
        required={**technical.required, **creative.required},
        observations=technical.observations + creative.observations,
        changed_ranges=creative.changed_ranges,
        binding=creative.binding or technical.binding,
    )
