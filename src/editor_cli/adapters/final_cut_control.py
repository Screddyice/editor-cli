"""Final Cut control backed by the fixed native helper protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, TypeVar

from fcpxml.parser import FCPXMLParser

from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.adapters.native_final_cut import (
    NativeFinalCutClient,
    NativeFinalCutError,
    ShareReceipt,
)
from editor_cli.session.models import ProjectIdentity

T = TypeVar("T")


class FinalCutControlError(RuntimeError):
    """Raised when native Final Cut control cannot prove its postcondition."""


@dataclass(frozen=True)
class DiagnosticProxyReceipt:
    """A proxy render that cannot serve as review evidence."""

    kind: Literal["diagnostic_proxy"]
    output: Path
    review_eligible: Literal[False] = False


class FinalCutControl:
    def __init__(
        self,
        native: NativeFinalCutClient,
        fcpxml: FCPXMLMCPClient,
        *,
        session_root: Path,
    ):
        self.native = native
        self.fcpxml = fcpxml
        try:
            root = session_root.expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise FinalCutControlError("Final Cut session root is invalid") from exc
        if not root.is_absolute() or root == Path(root.anchor):
            raise FinalCutControlError("Final Cut session root is invalid")
        self.session_root = root

    async def active_projects(self) -> tuple[ProjectIdentity, ...]:
        probe = await self._call_native(self.native.probe, self.session_root)
        return (probe.active_project,) if probe.active_project is not None else ()

    async def export_xml(self, identity: ProjectIdentity, destination: Path) -> None:
        output = destination.expanduser().resolve()
        await self._call_native(
            self.native.export_xml, identity, output, self.session_root
        )

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

    async def duplicate_project(
        self, identity: ProjectIdentity, name: str
    ) -> ProjectIdentity:
        return await self._call_native(
            self.native.duplicate_project,
            identity,
            name,
            self.session_root,
        )

    async def import_project(
        self, path: Path, expected_identity: ProjectIdentity
    ) -> ProjectIdentity:
        source = path.expanduser().resolve()
        return await self._call_native(
            self.native.import_xml,
            expected_identity,
            source,
            self.session_root,
        )

    async def render_preview(
        self, identity: ProjectIdentity, destination: Path
    ) -> ShareReceipt:
        output = destination.expanduser().resolve()
        return await self._call_native(
            self.native.share_preview,
            identity,
            output,
            self.session_root,
        )

    async def render_diagnostic_proxy(
        self, candidate: Path, destination: Path
    ) -> DiagnosticProxyReceipt:
        """Render a debugging proxy that the review path cannot accept."""
        source = self._session_path(candidate)
        output = self._session_path(destination)
        if not source.is_file():
            raise FinalCutControlError(
                "Candidate XML for diagnostic proxy is unavailable"
            )
        await self.fcpxml.call(
            "preview",
            {
                "action": "preview_render",
                "args": {
                    "filepath": str(source),
                    "output_path": str(output),
                    "height": 720,
                },
            },
        )
        if not output.is_file():
            raise FinalCutControlError(
                "FCPXML diagnostic proxy renderer did not create a video"
            )
        return DiagnosticProxyReceipt("diagnostic_proxy", output)

    async def open_project(self, identity: ProjectIdentity) -> ProjectIdentity:
        return await self._call_native(
            self.native.open_project, identity, self.session_root
        )

    def _session_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved == self.session_root or not resolved.is_relative_to(
            self.session_root
        ):
            raise FinalCutControlError("Diagnostic proxy path is outside the session")
        return resolved

    @staticmethod
    async def _call_native(operation: Callable[..., T], *args: Any) -> T:
        try:
            return await asyncio.to_thread(operation, *args)
        except NativeFinalCutError as exc:
            raise FinalCutControlError(str(exc)) from exc
