"""FFmpeg and FCPXML integrity checks for rendered Final Cut previews."""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fcpxml.parser import FCPXMLParser
from fcpxml.safe_xml import safe_parse

from editor_cli.verification.review import ReviewReport

Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def run_command(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@dataclass(frozen=True)
class CandidateFCPXMLInspection:
    duration_seconds: float | None
    media_references: tuple[Path, ...]
    required: dict[str, bool]
    observations: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return bool(self.required) and all(self.required.values())


@dataclass(frozen=True)
class CandidateInspection:
    expected_duration: float | None
    fcpxml_valid: bool
    media_references: tuple[Path, ...]
    report: ReviewReport


def _validation_text(value: dict) -> str:
    text = value.get("text")
    if isinstance(text, str):
        return text
    return ""


def _validation_passed(value: dict) -> bool:
    text = _validation_text(value)
    match = re.search(r"## Health Score:\s*(\d+)%", text)
    return bool(match and int(match.group(1)) > 0 and "[ERROR]" not in text)


def _media_path(source: str) -> Path | None:
    parsed = urlsplit(source)
    if parsed.scheme == "file" and parsed.hostname in {None, "", "localhost"}:
        return Path(unquote(parsed.path)).expanduser().resolve()
    if not parsed.scheme:
        path = Path(unquote(parsed.path)).expanduser()
        if path.is_absolute():
            return path.resolve()
    return None


def inspect_candidate_fcpxml(
    candidate: Path,
    *,
    upstream_validation: dict,
) -> CandidateFCPXMLInspection:
    """Parse one candidate with the pinned parser and verify referenced media."""
    candidate = candidate.expanduser().resolve()
    observations: list[str] = []
    required = {
        "fcpxml_parseable": False,
        "timeline_valid": False,
        "media_online": False,
    }
    try:
        parser = FCPXMLParser()
        project = parser.parse_file(str(candidate))
        if len(project.timelines) != 1:
            observations.append("Candidate FCPXML must contain exactly one timeline")
            return CandidateFCPXMLInspection(None, (), required, tuple(observations))
        duration = float(project.timelines[0].duration.seconds)
        if not math.isfinite(duration) or duration <= 0:
            observations.append(
                "Candidate timeline duration must be positive and finite"
            )
            return CandidateFCPXMLInspection(None, (), required, tuple(observations))
        required["fcpxml_parseable"] = True
        required["timeline_valid"] = _validation_passed(upstream_validation)
        if not required["timeline_valid"]:
            observations.append("FCPXML timeline validation did not pass")

        tree = safe_parse(str(candidate))
        sources = {
            resource["src"]
            for resource in parser.resources.values()
            if isinstance(resource.get("src"), str) and resource["src"]
        }
        sources.update(
            source
            for element in tree.getroot().findall(".//media-rep")
            if (source := element.get("src"))
        )
        references = tuple(
            sorted(
                {
                    path
                    for source in sources
                    for path in [_media_path(source)]
                    if path is not None
                }
            )
        )
        unsupported = sorted(
            {source for source in sources if _media_path(source) is None}
        )
        missing = tuple(path for path in references if not path.is_file())
        required["media_online"] = not missing and not unsupported
        observations.extend(f"Candidate media is missing: {path}" for path in missing)
        observations.extend(
            f"Candidate media reference is unsupported: {source}"
            for source in unsupported
        )
        return CandidateFCPXMLInspection(
            duration, references, required, tuple(observations)
        )
    except (
        OSError,
        RuntimeError,
        SyntaxError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ) as exc:
        observations.append(f"Candidate FCPXML could not be parsed: {exc}")
        return CandidateFCPXMLInspection(None, (), required, tuple(observations))


def inspect_candidate(
    candidate: Path,
    preview: Path,
    *,
    expected_source_duration: float,
    upstream_validation: dict,
    runner: Runner = run_command,
) -> CandidateInspection:
    """Inspect candidate XML and bind preview duration to that candidate."""
    del expected_source_duration
    candidate_qc = inspect_candidate_fcpxml(
        candidate, upstream_validation=upstream_validation
    )
    preview_qc = inspect_preview(
        preview,
        runner=runner,
        expected_duration=candidate_qc.duration_seconds,
        fcpxml_qc=candidate_qc.verified,
    )
    report = ReviewReport(
        required=dict(preview_qc.required),
        observations=candidate_qc.observations + preview_qc.observations,
    )
    return CandidateInspection(
        expected_duration=candidate_qc.duration_seconds,
        fcpxml_valid=candidate_qc.verified,
        media_references=candidate_qc.media_references,
        report=report,
    )


def inspect_preview(
    preview: Path,
    *,
    runner: Runner = run_command,
    expected_duration: float | None = None,
    duration_tolerance: float = 0.25,
    fcpxml_qc: bool | None = None,
    allow_black: bool = False,
    allow_silence: bool = False,
    expected_audio: bool = False,
) -> ReviewReport:
    preview = preview.expanduser().resolve()
    required: dict[str, bool] = {}
    observations: list[str] = []

    probe = runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(preview),
        ],
        60,
    )
    try:
        metadata = json.loads(probe.stdout) if probe.returncode == 0 else {}
    except json.JSONDecodeError:
        metadata = {}
    streams = metadata.get("streams", []) if isinstance(metadata, dict) else []
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    try:
        duration = float(metadata.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0

    readable = bool(video_streams) and probe.returncode == 0
    required["readable_video"] = readable
    required["nonzero_duration"] = duration > 0
    if not readable:
        observations.append("Preview has no readable video stream")
    if duration <= 0:
        observations.append("Preview duration is zero or unavailable")

    if expected_duration is not None:
        required["duration_matches"] = (
            abs(duration - expected_duration) <= duration_tolerance
        )
        if not required["duration_matches"]:
            observations.append(
                f"Preview duration {duration:.3f}s differs from expected "
                f"{expected_duration:.3f}s"
            )

    if expected_audio:
        required["audio_present"] = bool(audio_streams)
        if not audio_streams:
            observations.append("Preview has no audio stream")

    black_found = not readable
    silence_found = not readable
    if readable:
        black = runner(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(preview),
                "-vf",
                "blackdetect=d=0.5:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ],
            300,
        )
        black_found = black.returncode != 0 or "black_start:" in black.stderr
        silence_found = False
        if audio_streams:
            silence = runner(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    str(preview),
                    "-af",
                    "silencedetect=n=-50dB:d=1",
                    "-vn",
                    "-f",
                    "null",
                    "-",
                ],
                300,
            )
            silence_found = (
                silence.returncode != 0 or "silence_start:" in silence.stderr
            )

    required["no_unexpected_black"] = allow_black or not black_found
    required["no_unexpected_silence"] = allow_silence or not silence_found
    if black_found and not allow_black:
        observations.append("Preview contains an unexpected black interval")
    if silence_found and not allow_silence:
        observations.append("Preview contains an unexpected silent interval")

    if fcpxml_qc is not None:
        required["fcpxml_valid"] = fcpxml_qc
        if not fcpxml_qc:
            observations.append("FCPXML quality control failed")

    return ReviewReport(required=required, observations=tuple(observations))
