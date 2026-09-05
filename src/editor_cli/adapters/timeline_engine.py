"""FCPXML MCP-backed timeline analysis and edit application."""

from __future__ import annotations

from pathlib import Path

from fcpxml.parser import FCPXMLParser
from fcpxml.safe_xml import safe_parse

from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.session.models import EditProgram


class TimelineEngineError(RuntimeError):
    """Raised when an FCPXML operation does not create its promised output."""


class FCPXMLTimelineEngine:
    def __init__(self, client: FCPXMLMCPClient):
        self.client = client

    async def analyze(self, source: Path) -> dict:
        analysis = await self.client.call(
            "inspect",
            {"action": "analyze_timeline", "args": {"filepath": str(source)}},
        )
        gaps = await self.client.call(
            "diagnose", {"action": "detect_gaps", "args": {"filepath": str(source)}}
        )
        project = FCPXMLParser().parse_file(str(source))
        if len(project.timelines) != 1:
            raise TimelineEngineError("The captured FCPXML must contain one timeline")
        timeline = project.timelines[0]
        tree = safe_parse(str(source))
        return {
            "project": timeline.name,
            "duration_seconds": timeline.duration.seconds,
            "width": timeline.width,
            "height": timeline.height,
            "frame_rate": timeline.frame_rate,
            "clips": timeline.total_clips,
            "gaps": len(tree.getroot().findall(".//gap")),
            "analysis_report": analysis.get("text", analysis),
            "gap_report": gaps.get("text", gaps),
        }

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
