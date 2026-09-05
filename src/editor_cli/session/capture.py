"""Identity-checked capture and preservation of the active Final Cut project."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from fcpxml.safe_xml import safe_parse

from editor_cli.session.models import ProjectIdentity
from editor_cli.session.paths import SessionPaths
from editor_cli.session.store import SessionStore


class CaptureError(RuntimeError):
    """Raised when the active Final Cut project cannot be captured safely."""


class FinalCutControl(Protocol):
    async def active_projects(self) -> Sequence[ProjectIdentity]: ...

    async def export_xml(
        self, identity: ProjectIdentity, destination: Path
    ) -> None: ...

    async def inspect_xml(self, path: Path): ...

    async def duplicate_project(
        self, identity: ProjectIdentity, name: str
    ) -> ProjectIdentity: ...


@dataclass(frozen=True)
class CaptureResult:
    identity: ProjectIdentity
    source_xml: Path
    source_sha256: str
    preserved_name: str
    media_references: tuple[Path, ...]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_media_references(path: Path) -> tuple[Path, ...]:
    tree = safe_parse(str(path))
    references: set[Path] = set()
    for media_rep in tree.getroot().findall(".//media-rep"):
        source = media_rep.get("src")
        if not source:
            continue
        parsed = urlsplit(source)
        if parsed.scheme != "file" or parsed.hostname not in {None, "", "localhost"}:
            continue
        candidate = Path(unquote(parsed.path)).expanduser().resolve()
        if candidate.is_absolute():
            references.add(candidate)
    return tuple(sorted(references))


async def capture_active_project(
    control: FinalCutControl,
    paths: SessionPaths,
    store: SessionStore | None = None,
) -> CaptureResult:
    if store is not None and store.pending_actions():
        raise CaptureError(
            "A pending Final Cut action requires reconciliation before capture can resume"
        )

    projects = tuple(await control.active_projects())
    if len(projects) != 1:
        raise CaptureError("Open Final Cut and select one project before editing")
    identity = projects[0]

    export_path = paths.source / "active-source.fcpxml"
    export_token = (
        store.begin_external_action(
            "finalcut.export_xml",
            {"project": identity.project, "destination": str(export_path)},
            expected_identity={
                "library": identity.library,
                "event": identity.event,
                "project": identity.project,
                "duration_seconds": identity.duration_seconds,
            },
            idempotency={"destination": str(export_path)},
        )
        if store is not None
        else None
    )
    await control.export_xml(identity, export_path)
    parsed = await control.inspect_xml(export_path)
    if parsed.project != identity.project:
        raise CaptureError(
            "Exported project identity does not match the active project"
        )
    if abs(parsed.duration_seconds - identity.duration_seconds) > parsed.frame_seconds:
        raise CaptureError(
            "Exported timeline duration does not match the active project"
        )

    source_sha256 = file_sha256(export_path)
    media_references = extract_media_references(export_path)
    paths.add_media_references(media_references)
    if store is not None and export_token is not None:
        store.complete_external_action(
            export_token,
            {
                "path": str(export_path),
                "sha256": source_sha256,
                "identity": {
                    "library": identity.library,
                    "event": identity.event,
                    "project": identity.project,
                    "duration_seconds": identity.duration_seconds,
                },
                "media_references": [str(path) for path in media_references],
            },
        )

    short_session = paths.root.name[:8]
    preserved = (
        f"{identity.project} - Before AI - {short_session} - "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H-%M}"
    )
    preserved_identity = ProjectIdentity(
        library=identity.library,
        event=identity.event,
        project=preserved,
        duration_seconds=identity.duration_seconds,
    )
    duplicate_token = (
        store.begin_external_action(
            "finalcut.duplicate_project",
            {"project": identity.project, "preserved_name": preserved},
            expected_identity={
                "library": preserved_identity.library,
                "event": preserved_identity.event,
                "project": preserved_identity.project,
                "duration_seconds": preserved_identity.duration_seconds,
            },
            idempotency={"project_name": preserved},
        )
        if store is not None
        else None
    )
    duplicated = await control.duplicate_project(identity, preserved)
    if duplicated != preserved_identity:
        raise CaptureError("Final Cut did not create the exact preserved project")
    if store is not None and duplicate_token is not None:
        store.complete_external_action(
            duplicate_token,
            {
                "preserved_name": preserved,
                "identity": {
                    "library": duplicated.library,
                    "event": duplicated.event,
                    "project": duplicated.project,
                    "duration_seconds": duplicated.duration_seconds,
                },
            },
        )

    return CaptureResult(
        identity=identity,
        source_xml=export_path,
        source_sha256=source_sha256,
        preserved_name=preserved,
        media_references=media_references,
    )


async def recover_active_project_capture(
    control: FinalCutControl,
    paths: SessionPaths,
    store: SessionStore,
) -> CaptureResult:
    """Recover a capture after all pending actions have been reconciled."""

    if store.pending_actions():
        raise CaptureError(
            "A pending Final Cut action requires reconciliation before capture can resume"
        )
    export = _completed_capture_action(
        store, {"finalcut.export_xml", "finalcut.export", "commandpost.export"}
    )
    if export is None:
        raise CaptureError("Capture recovery has no completed export receipt")
    try:
        expected = _capture_identity(export["expected"]["identity"])
        result = export["result"]
        if not isinstance(result, dict) or result.get("identity") != expected.__dict__:
            raise ValueError
        export_path = Path(result["path"]).expanduser().resolve()
        expected_path = paths.source / "active-source.fcpxml"
        if export_path != expected_path or not export_path.is_file():
            raise ValueError
        source_sha256 = file_sha256(export_path)
        if result.get("sha256") != source_sha256:
            raise ValueError
        parsed = await control.inspect_xml(export_path)
        if (
            parsed.project != expected.project
            or parsed.duration_seconds != expected.duration_seconds
        ):
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise CaptureError("Completed export receipt is malformed or stale") from exc

    media_references = extract_media_references(export_path)
    paths.add_media_references(media_references)
    duplicate = _completed_capture_action(
        store,
        {
            "finalcut.duplicate_project",
            "finalcut.duplicate",
            "commandpost.duplicate",
        },
    )
    if duplicate is None:
        active = tuple(await control.active_projects())
        if len(active) != 1 or active[0] != expected:
            raise CaptureError(
                "Capture recovery did not find the exact exported project identity"
            )
        preserved = (
            f"{expected.project} - Before AI - {paths.root.name[:8]} - "
            f"{datetime.now(timezone.utc):%Y-%m-%d %H-%M}"
        )
        preserved_identity = ProjectIdentity(
            expected.library,
            expected.event,
            preserved,
            expected.duration_seconds,
        )
        token = store.begin_external_action(
            "finalcut.duplicate_project",
            {"project": expected.project, "preserved_name": preserved},
            expected_identity=preserved_identity.__dict__,
            idempotency={"project_name": preserved},
        )
        duplicated = await control.duplicate_project(expected, preserved)
        if duplicated != preserved_identity:
            raise CaptureError("Final Cut did not create the exact preserved project")
        store.complete_external_action(
            token,
            {"preserved_name": preserved, "identity": duplicated.__dict__},
        )
    else:
        try:
            preserved_identity = _capture_identity(duplicate["expected"]["identity"])
            result = duplicate["result"]
            if (
                not isinstance(result, dict)
                or result.get("identity") != preserved_identity.__dict__
                or result.get("preserved_name") != preserved_identity.project
            ):
                raise ValueError
            active = tuple(await control.active_projects())
            if len(active) != 1 or active[0] != preserved_identity:
                raise ValueError
            preserved = preserved_identity.project
        except (KeyError, TypeError, ValueError) as exc:
            raise CaptureError(
                "Completed duplicate receipt is malformed or ambiguous"
            ) from exc

    return CaptureResult(
        identity=expected,
        source_xml=export_path,
        source_sha256=source_sha256,
        preserved_name=preserved,
        media_references=media_references,
    )


def _completed_capture_action(
    store: SessionStore, names: set[str]
) -> dict[str, Any] | None:
    completed: list[dict[str, Any]] = []
    for event in store.events():
        if event.get("kind") != "external_action":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            raise CaptureError("Capture journal contains malformed action data")
        if data.get("action") in names and data.get("status") == "complete":
            required = {
                "token",
                "action",
                "arguments",
                "expected",
                "status",
                "result",
            }
            if set(data) != required:
                raise CaptureError("Capture journal contains malformed action data")
            completed.append(data)
    if len(completed) > 1:
        raise CaptureError("Capture journal contains ambiguous completed actions")
    return completed[0] if completed else None


def _capture_identity(value: object) -> ProjectIdentity:
    if not isinstance(value, dict) or set(value) != {
        "library",
        "event",
        "project",
        "duration_seconds",
    }:
        raise ValueError
    return ProjectIdentity(**value)
