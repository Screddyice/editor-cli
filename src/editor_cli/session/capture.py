"""Identity-checked capture and preservation of the active Final Cut project."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from editor_cli.session.models import ProjectIdentity
from editor_cli.session.paths import SessionPaths
from editor_cli.session.store import SessionStore


class CaptureError(RuntimeError):
    """Raised when the active Final Cut project cannot be captured safely."""


class FinalCutControl(Protocol):
    async def active_projects(self) -> Sequence[ProjectIdentity]: ...

    async def export_xml(self, identity: ProjectIdentity, destination: Path) -> None: ...

    async def inspect_xml(self, path: Path): ...

    async def duplicate_project(self, identity: ProjectIdentity, name: str) -> None: ...


@dataclass(frozen=True)
class CaptureResult:
    identity: ProjectIdentity
    source_xml: Path
    source_sha256: str
    preserved_name: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def capture_active_project(
    control: FinalCutControl,
    paths: SessionPaths,
    store: SessionStore | None = None,
) -> CaptureResult:
    projects = tuple(await control.active_projects())
    if len(projects) != 1:
        raise CaptureError("Open Final Cut and select one project before editing")
    identity = projects[0]

    if store is not None and store.pending_actions():
        raise CaptureError(
            "A pending Final Cut action requires reconciliation before capture can resume"
        )

    export_path = paths.source / "active-source.fcpxml"
    export_token = (
        store.begin_external_action(
            "commandpost.export",
            {"project": identity.project, "destination": str(export_path)},
        )
        if store is not None
        else None
    )
    await control.export_xml(identity, export_path)
    if store is not None and export_token is not None:
        store.complete_external_action(export_token, {"path": str(export_path)})

    parsed = await control.inspect_xml(export_path)
    if parsed.project != identity.project:
        raise CaptureError("Exported project identity does not match the active project")
    if abs(parsed.duration_seconds - identity.duration_seconds) > parsed.frame_seconds:
        raise CaptureError("Exported timeline duration does not match the active project")

    preserved = f"{identity.project} - Before AI - {datetime.now():%Y-%m-%d %H-%M}"
    duplicate_token = (
        store.begin_external_action(
            "commandpost.duplicate",
            {"project": identity.project, "preserved_name": preserved},
        )
        if store is not None
        else None
    )
    await control.duplicate_project(identity, preserved)
    if store is not None and duplicate_token is not None:
        store.complete_external_action(
            duplicate_token, {"preserved_name": preserved}
        )

    return CaptureResult(
        identity=identity,
        source_xml=export_path,
        source_sha256=file_sha256(export_path),
        preserved_name=preserved,
    )
