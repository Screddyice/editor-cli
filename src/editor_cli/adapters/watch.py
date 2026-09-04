"""Rendered-video evidence extraction through the shared ``watch`` skill."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


FRAME_LINE = re.compile(
    r"^- `(?P<path>[^`]+)` \(t=(?P<time>[^,]+), reason=(?P<reason>[^)]+)\)$",
    re.MULTILINE,
)
TRANSCRIPT_BLOCK = re.compile(
    r"## Transcript\s+.*?```\n(?P<text>.*?)\n```", re.DOTALL
)


def run_command(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seconds(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return float(value)


def _atomic_json_write(path: Path, value: dict) -> None:
    fd, raw = tempfile.mkstemp(prefix="manifest-", suffix=".json", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass(frozen=True)
class EvidenceFrame:
    path: Path
    timestamp_seconds: float
    reason: str
    scope: str


@dataclass(frozen=True)
class EvidenceBundle:
    manifest: Path
    preview: Path
    preview_sha256: str
    frames: tuple[EvidenceFrame, ...]
    transcript: str
    changed_ranges: tuple[tuple[float, float], ...]

    @classmethod
    def from_manifest(cls, path: Path) -> "EvidenceBundle":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            manifest=path,
            preview=Path(data["preview"]["path"]),
            preview_sha256=data["preview"]["sha256"],
            frames=tuple(
                EvidenceFrame(
                    path=Path(frame["path"]),
                    timestamp_seconds=float(frame["timestamp_seconds"]),
                    reason=frame["reason"],
                    scope=frame["scope"],
                )
                for frame in data["frames"]
            ),
            transcript=data["transcript"],
            changed_ranges=tuple(tuple(item) for item in data["changed_ranges"]),
        )


class WatchAdapter:
    def __init__(
        self,
        script: Path,
        runner: Callable[[list[str], int], subprocess.CompletedProcess[str]] = run_command,
    ):
        self.script = script.expanduser().resolve()
        if not self.script.is_file():
            raise FileNotFoundError(f"watch.py not found: {self.script}")
        self.runner = runner

    def analyze(
        self,
        preview: Path,
        out: Path,
        changed_ranges: Sequence[tuple[float, float]],
    ) -> EvidenceBundle:
        preview = preview.expanduser().resolve()
        if not preview.is_file():
            raise FileNotFoundError(f"Preview not found: {preview}")
        out = out.expanduser().resolve()
        out.mkdir(mode=0o700, parents=True, exist_ok=True)

        reports: list[dict[str, str]] = []
        frames: list[dict[str, object]] = []
        full_dir = out / "full"
        full = self._invoke(
            preview,
            full_dir,
            ["--detail", "balanced", "--max-frames", "100"],
        )
        reports.append({"scope": "full", "path": str(full_dir / "report.md")})
        frames.extend(self._parse_frames(full.stdout, out, "full"))
        transcript_match = TRANSCRIPT_BLOCK.search(full.stdout)
        transcript = transcript_match.group("text") if transcript_match else ""

        normalized_ranges: list[tuple[float, float]] = []
        for index, (start, end) in enumerate(changed_ranges, 1):
            if start < 0 or end <= start:
                raise ValueError(f"Invalid changed range: {start}-{end}")
            normalized_ranges.append((float(start), float(end)))
            focus_start = max(0.0, float(start) - 2.0)
            focus_end = float(end) + 2.0
            cap = min(100, max(1, math.ceil((focus_end - focus_start) * 2)))
            range_dir = out / "ranges" / f"range-{index:03d}"
            focused = self._invoke(
                preview,
                range_dir,
                [
                    "--detail",
                    "balanced",
                    "--max-frames",
                    str(cap),
                    "--fps",
                    "2",
                    "--start",
                    str(focus_start),
                    "--end",
                    str(focus_end),
                    "--no-whisper",
                ],
            )
            scope = f"{focus_start:.3f}-{focus_end:.3f}"
            reports.append({"scope": scope, "path": str(range_dir / "report.md")})
            frames.extend(self._parse_frames(focused.stdout, out, scope))

        manifest = out / "manifest.json"
        _atomic_json_write(
            manifest,
            {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "preview": {"path": str(preview), "sha256": _sha256(preview)},
                "frames": frames,
                "transcript": transcript,
                "changed_ranges": normalized_ranges,
                "reports": reports,
            },
        )
        return EvidenceBundle.from_manifest(manifest)

    def _invoke(
        self, preview: Path, out: Path, options: list[str]
    ) -> subprocess.CompletedProcess[str]:
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        args = [
            sys.executable,
            str(self.script),
            str(preview),
            *options,
            "--out-dir",
            str(out),
        ]
        completed = self.runner(args, 900)
        (out / "report.md").write_text(completed.stdout, encoding="utf-8")
        return completed

    @staticmethod
    def _parse_frames(report: str, evidence_root: Path, scope: str) -> list[dict]:
        frames: list[dict] = []
        for match in FRAME_LINE.finditer(report):
            path = Path(match.group("path")).expanduser().resolve()
            if not path.is_file() or not path.is_relative_to(evidence_root):
                raise ValueError(f"watch returned a frame outside the evidence bundle: {path}")
            frames.append(
                {
                    "path": str(path),
                    "timestamp_seconds": _seconds(match.group("time")),
                    "reason": match.group("reason"),
                    "scope": scope,
                }
            )
        return frames
