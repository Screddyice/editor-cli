"""FFmpeg and FCPXML integrity checks for rendered Final Cut previews."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

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
