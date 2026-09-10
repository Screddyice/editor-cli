"""FCPXML MCP-backed timeline analysis and edit application."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

from fcpxml.parser import FCPXMLParser
from fcpxml.safe_xml import safe_parse

from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.session.models import EditProgram


class TimelineEngineError(RuntimeError):
    """Raised when an FCPXML operation does not create its promised output."""


@dataclass(frozen=True)
class MediaRegistration:
    asset_id: str
    path: Path


class FCPXMLTimelineEngine:
    def __init__(self, client: FCPXMLMCPClient):
        self.client = client

    async def analyze(self, source: Path) -> dict:
        async def read(group: str, action: str) -> dict:
            return await self.client.call(
                group, {"action": action, "args": {"filepath": str(source)}}
            )

        analysis = await read("inspect", "analyze_timeline")
        clip_report = await read("inspect", "list_clips")
        marker_report = await read("inspect", "list_markers")
        role_report = await read("inspect", "list_roles")
        effect_report = await read("inspect", "list_effects")
        pacing_report = await read("inspect", "analyze_pacing")
        gap_report = await self.client.call(
            "diagnose", {"action": "detect_gaps", "args": {"filepath": str(source)}}
        )
        transcript_report = await read("transcript", "transcript_pack")
        index_report = await self.client.call(
            "index", {"action": "index_status", "args": {}}
        )
        project = FCPXMLParser().parse_file(str(source))
        if len(project.timelines) != 1:
            raise TimelineEngineError("The captured FCPXML must contain one timeline")
        timeline = project.timelines[0]
        tree = safe_parse(str(source))
        root = tree.getroot()

        def seconds(value: str | None) -> float:
            if not value:
                return 0.0
            raw = value.removesuffix("s")
            return float(Fraction(raw))

        clips = []
        for node in root.findall(".//spine/*"):
            if node.tag not in {"asset-clip", "clip", "mc-clip", "sync-clip"}:
                continue
            clips.append(
                {
                    "id": node.get("ref") or node.get("id"),
                    "name": node.get("name", ""),
                    "offset_seconds": seconds(node.get("offset")),
                    "duration_seconds": seconds(node.get("duration")),
                    "role": node.get("role"),
                }
            )
        gaps = [
            {
                "name": node.get("name", ""),
                "offset_seconds": seconds(node.get("offset")),
                "duration_seconds": seconds(node.get("duration")),
            }
            for node in root.findall(".//gap")
        ]
        role_counts: dict[tuple[str, str | None], int] = {}
        for node in root.iter():
            for attribute, kind in (
                ("role", None),
                ("audioRole", "audio"),
                ("videoRole", "video"),
            ):
                role = node.get(attribute, "").split(".", 1)[0]
                if role:
                    key = (role, kind)
                    role_counts[key] = role_counts.get(key, 0) + 1
        markers = [
            {
                "name": node.get("value", ""),
                "start_seconds": seconds(node.get("start")),
                "duration_seconds": seconds(node.get("duration")),
            }
            for node in root.findall(".//marker")
        ]
        effects = [
            {
                "id": node.get("id"),
                "name": node.get("name", ""),
                "uid": node.get("uid", ""),
            }
            for node in root.findall(".//resources/effect")
        ]
        title_text = [
            {"text": "".join(node.itertext()).strip()}
            for node in root.findall(".//title//text-style")
            if "".join(node.itertext()).strip()
        ]
        transcript_text = transcript_report.get("text")
        transcript = (
            [{"text": transcript_text, "source": "transcript_pack"}]
            if isinstance(transcript_text, str) and transcript_text.strip()
            else []
        )
        return {
            "project": timeline.name,
            "duration_seconds": timeline.duration.seconds,
            "width": timeline.width,
            "height": timeline.height,
            "frame_rate": timeline.frame_rate,
            "clips": clips,
            "gaps": gaps,
            "roles": [
                {
                    **{"name": name, "clip_count": count},
                    **({"kind": kind} if kind else {}),
                }
                for (name, kind), count in sorted(
                    role_counts.items(), key=lambda item: (item[0][0], item[0][1] or "")
                )
            ],
            "markers": markers,
            "effects": effects,
            "transcript": transcript,
            "title_text": title_text,
            "pacing": {
                "clip_count": len(clips),
                "average_clip_seconds": (
                    sum(item["duration_seconds"] for item in clips) / len(clips)
                    if clips
                    else 0.0
                ),
            },
            "analysis_report": analysis.get("text", analysis),
            "clip_report": clip_report.get("text", clip_report),
            "marker_report": marker_report.get("text", marker_report),
            "role_report": role_report.get("text", role_report),
            "effect_report": effect_report.get("text", effect_report),
            "pacing_report": pacing_report.get("text", pacing_report),
            "gap_report": gap_report.get("text", gap_report),
            "transcript_report": transcript_report.get("text", transcript_report),
            "index_report": index_report.get("text", index_report),
        }

    async def register_media(
        self,
        source: Path,
        media: Path,
        destination: Path,
        *,
        name: str,
        duration_seconds: float,
        has_video: bool,
        has_audio: bool,
    ) -> MediaRegistration:
        from math import isfinite

        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not isfinite(duration_seconds)
            or duration_seconds <= 0
        ):
            raise TimelineEngineError("Registered media duration must be positive")
        source = source.resolve()
        media = media.resolve()
        destination = destination.resolve()
        tree = ET.parse(source)
        resources = tree.find("./resources")
        if resources is None:
            raise TimelineEngineError("FCPXML has no resources section")
        asset_id = f"r_editor_{sha256(str(media).encode()).hexdigest()[:12]}"
        if tree.find(f'.//asset[@id="{asset_id}"]') is None:
            duration = Fraction(duration_seconds).limit_denominator(100000)
            asset = ET.SubElement(
                resources,
                "asset",
                {
                    "id": asset_id,
                    "name": name,
                    "duration": f"{duration.numerator}/{duration.denominator}s"
                    if duration.denominator != 1
                    else f"{duration.numerator}s",
                    "hasVideo": "1" if has_video else "0",
                    "hasAudio": "1" if has_audio else "0",
                },
            )
            ET.SubElement(
                asset, "media-rep", {"kind": "original-media", "src": media.as_uri()}
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        tree.write(destination, encoding="utf-8", xml_declaration=True)
        return MediaRegistration(asset_id, destination)

    async def apply(
        self, source: Path, program: EditProgram, destination: Path
    ) -> Path:
        source = source.expanduser().resolve()
        destination = destination.expanduser().resolve()
        current = source
        intermediates: list[Path] = []
        for index, operation in enumerate(program.operations, 1):
            output = (
                destination
                if index == len(program.operations)
                else destination.with_name(
                    f".{destination.stem}.step-{index:02d}.fcpxml"
                )
            )
            if (
                "filepath" in operation.arguments
                or "output_path" in operation.arguments
            ):
                raise TimelineEngineError(
                    "Edit operation arguments cannot override controller paths"
                )
            args = dict(operation.arguments)
            if operation.group == "generate" and operation.action == "apply_template":
                tree = ET.parse(current)
                resolved_clips = {}
                for slot, clip in args["clips"].items():
                    node = tree.find(f'.//asset[@id="{clip["asset_id"]}"]')
                    media_rep = node.find("media-rep") if node is not None else None
                    src = media_rep.get("src") if media_rep is not None else None
                    if not src:
                        raise TimelineEngineError(
                            f"Unknown or non-media asset_id: {clip['asset_id']}"
                        )
                    parsed = urlparse(src)
                    canonical = (
                        Path(unquote(parsed.path)).resolve()
                        if parsed.scheme == "file"
                        else None
                    )
                    if canonical is None or not canonical.is_file():
                        raise TimelineEngineError(
                            f"Asset is not an available local file: {clip['asset_id']}"
                        )
                    resolved_clips[slot] = {**clip, "src": str(canonical)}
                    resolved_clips[slot].pop("asset_id")
                args["clips"] = resolved_clips
            if operation.group == "generate" and operation.action == "apply_template":
                args["output_path"] = str(output)
            else:
                args.update({"filepath": str(current), "output_path": str(output)})
            await self.client.call(
                operation.group, {"action": operation.action, "args": args}
            )
            if not output.is_file():
                raise TimelineEngineError(
                    f"FCPXML operation did not create {output.name}"
                )
            if current in intermediates:
                current.unlink(missing_ok=True)
            if output != destination:
                intermediates.append(output)
            current = output
        return destination
