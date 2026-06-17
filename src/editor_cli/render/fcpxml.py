"""EDL → FCPXML (Final Cut Pro 12.2, DTD v1.14).

Emits a self-contained ``.fcpxml`` describing a timeline of asset-clips that FCP
imports as an editable project. Pure function: media durations are supplied by
the caller (the orchestrator already probes them) so this module needs no
ffmpeg/filesystem access and stays unit-testable.

Time is expressed as FCP rational seconds ``numerator/denominators`` with a
timebase of ``fps*100`` so whole-frame durations stay integer and frame-aligned.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

from editor_cli.domain.edl import EDL

FCPXML_VERSION = "1.14"


def _timebase(fps: float) -> int:
    return round(fps * 100)


def _t(seconds: float, fps: float) -> str:
    """Seconds → FCP rational time string, frame-aligned."""
    if seconds <= 0:
        return "0s"
    tb = _timebase(fps)
    frames = round(seconds * fps)
    return f"{frames * 100}/{tb}s"


def _file_url(src: str) -> str:
    return Path(src).absolute().as_uri()


def edl_to_fcpxml(
    edl: EDL,
    project_name: str = "Editor CLI",
    asset_durations: dict[str, float] | None = None,
    event_name: str = "Editor CLI",
) -> str:
    fps = edl.fps
    width, height = edl.resolution
    tb = _timebase(fps)
    frame_dur = f"100/{tb}s"
    durations = dict(asset_durations or {})

    # Unique source files in first-appearance order; r1 is reserved for format.
    srcs: list[str] = []
    for seg in edl.segments:
        if seg.src not in srcs:
            srcs.append(seg.src)
    asset_id = {src: f"r{i + 2}" for i, src in enumerate(srcs)}

    def src_duration(src: str) -> float:
        if src in durations:
            return durations[src]
        return max(s.out for s in edl.segments if s.src == src)

    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        f'<fcpxml version="{FCPXML_VERSION}">',
        "  <resources>",
        (
            f'    <format id="r1" name="FFVideoFormat{int(height)}p{round(fps)}" '
            f'frameDuration="{frame_dur}" width="{int(width)}" height="{int(height)}" '
            f'colorSpace="1-1-1 (Rec. 709)"/>'
        ),
    ]
    for src in srcs:
        aid = asset_id[src]
        name = Path(src).stem
        dur = _t(src_duration(src), fps)
        url = _file_url(src)
        out.append(
            f'    <asset id="{aid}" name={quoteattr(name)} start="0s" '
            f'duration="{dur}" hasVideo="1" hasAudio="1" videoSources="1" '
            f'audioSources="1" audioChannels="2" format="r1">'
        )
        out.append(f'      <media-rep kind="original-media" src={quoteattr(url)}/>')
        out.append("    </asset>")
    out.append("  </resources>")

    seq_dur = _t(sum(s.duration for s in edl.segments), fps)
    out += [
        "  <library>",
        f"    <event name={quoteattr(event_name)}>",
        f"      <project name={quoteattr(project_name)}>",
        (
            f'        <sequence format="r1" duration="{seq_dur}" tcStart="0s" '
            f'tcFormat="NDF" audioLayout="stereo" audioRate="48k">'
        ),
        "          <spine>",
    ]
    offset = 0.0
    for seg in edl.segments:
        aid = asset_id[seg.src]
        name = Path(seg.src).stem
        out.append(
            f'            <asset-clip ref="{aid}" offset="{_t(offset, fps)}" '
            f'name={quoteattr(name)} start="{_t(seg.in_, fps)}" '
            f'duration="{_t(seg.duration, fps)}" format="r1" tcFormat="NDF"/>'
        )
        offset += seg.duration
    out += [
        "          </spine>",
        "        </sequence>",
        "      </project>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
    ]
    return "\n".join(out) + "\n"
