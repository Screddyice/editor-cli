"""Concrete Final Cut control built from CommandPost and the FCPXML MCP."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fcpxml.live import list_fcp_libraries
from fcpxml.parser import FCPXMLParser
from fcpxml.safe_xml import safe_parse
from fcpxml.writer import write_fcpxml

from editor_cli.adapters.commandpost import CommandPostClient, CommandPostError
from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.session.models import ProjectIdentity


class FinalCutControlError(RuntimeError):
    """Raised when Final Cut state cannot be mapped to one safe project."""


class CommandPostFinalCutControl:
    def __init__(
        self,
        commandpost: CommandPostClient,
        fcpxml: FCPXMLMCPClient,
        *,
        library_reader: Callable[[], list[dict[str, Any]]] = list_fcp_libraries,
    ):
        self.commandpost = commandpost
        self.fcpxml = fcpxml
        self.library_reader = library_reader
        self._active_library_path: Path | None = None

    async def active_projects(self) -> Sequence[ProjectIdentity]:
        response = await self.commandpost.request(
            self.commandpost.controller_message("active_project")
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise FinalCutControlError("CommandPost returned no active-project state")
        project = result.get("project")
        duration = result.get("durationSeconds")
        if (
            not isinstance(project, str)
            or not project
            or not isinstance(duration, (int, float))
        ):
            raise FinalCutControlError(
                "CommandPost returned incomplete project identity"
            )

        matches: list[tuple[str, str]] = []
        for library in self.library_reader():
            for event in library.get("events", []):
                if project in event.get("projects", []):
                    matches.append((library["name"], event["name"]))
        if len(matches) != 1:
            raise FinalCutControlError(
                "The active project name must identify one open Final Cut event"
            )

        paths = [
            Path(value).expanduser().resolve()
            for value in result.get("libraryPaths", [])
            if isinstance(value, str)
        ]
        library_name, event_name = matches[0]
        matching_paths = [path for path in paths if path.stem == library_name]
        if len(matching_paths) != 1:
            raise FinalCutControlError(
                "CommandPost did not identify one path for the active Final Cut library"
            )
        self._active_library_path = matching_paths[0]
        return (
            ProjectIdentity(
                library=library_name,
                event=event_name,
                project=project,
                duration_seconds=float(duration),
            ),
        )

    async def export_xml(self, _identity: ProjectIdentity, destination: Path) -> None:
        destination = destination.expanduser().resolve()
        if destination.exists():
            raise FinalCutControlError("Refusing to replace an existing FCPXML export")
        await self.commandpost.request(
            self.commandpost.controller_message(
                "export_xml", destination=str(destination)
            )
        )
        if not destination.is_file():
            raise FinalCutControlError("Final Cut did not create the requested FCPXML")

    async def inspect_xml(self, path: Path):
        parsed = FCPXMLParser().parse_file(str(path))
        if len(parsed.timelines) != 1:
            raise FinalCutControlError("The exported FCPXML must contain one timeline")
        timeline = parsed.timelines[0]
        return SimpleNamespace(
            project=timeline.name,
            duration_seconds=timeline.duration.seconds,
            frame_seconds=1 / timeline.frame_rate,
        )

    async def duplicate_project(self, _identity: ProjectIdentity, name: str) -> None:
        await self.commandpost.request(
            self.commandpost.controller_message("duplicate_project", name=name)
        )

    async def import_project(self, path: Path, project_name: str) -> None:
        if self._active_library_path is None:
            await self.active_projects()
        assert self._active_library_path is not None
        imported = path.with_name(f"{path.stem}.import.fcpxml")
        if imported.exists():
            raise FinalCutControlError(
                "Refusing to replace an existing import candidate"
            )
        tree = safe_parse(str(path))
        projects = tree.getroot().findall(".//project")
        if len(projects) != 1:
            raise FinalCutControlError("The candidate FCPXML must contain one project")
        projects[0].set("name", project_name)
        write_fcpxml(tree.getroot(), str(imported))
        await self.fcpxml.call(
            "deliver",
            {
                "action": "push_to_fcp",
                "args": {
                    "filepath": str(imported),
                    "library_location": str(self._active_library_path),
                    "suppress_warnings": True,
                    "copy_assets": False,
                    "confirm_unreviewed": True,
                },
            },
        )

    async def render_preview(self, project_name: str, destination: Path) -> None:
        candidate = (
            destination.parent.parent
            / "candidates"
            / destination.with_suffix(".fcpxml").name
        )
        if not candidate.is_file():
            raise FinalCutControlError(
                f"Candidate XML for {project_name!r} is unavailable"
            )
        await self.fcpxml.call(
            "preview",
            {
                "action": "preview_render",
                "args": {
                    "filepath": str(candidate),
                    "output_path": str(destination),
                    "height": 720,
                },
            },
        )
        if not destination.is_file():
            raise FinalCutControlError("FCPXML preview renderer did not create a video")

    async def open_project(self, project_name: str) -> None:
        try:
            await self.commandpost.request(
                self.commandpost.controller_message("open_project", name=project_name)
            )
        except CommandPostError as exc:
            raise FinalCutControlError(str(exc)) from exc
