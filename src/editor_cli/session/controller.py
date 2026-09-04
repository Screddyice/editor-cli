"""Persisted three-pass controller for source-preserving Final Cut edits."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from editor_cli.config import ControllerConfig
from editor_cli.session.capture import capture_active_project, file_sha256
from editor_cli.session.models import EditProgram, EditRequest, ProjectIdentity, SessionState
from editor_cli.session.paths import SessionPaths
from editor_cli.session.store import SessionStore
from editor_cli.verification.review import ReviewReport


class SessionError(RuntimeError):
    """Raised when a session transition would violate the control loop."""


class TimelineEngine(Protocol):
    async def analyze(self, source: Path) -> dict[str, Any]: ...

    async def apply(
        self, source: Path, program: EditProgram, destination: Path
    ) -> Path: ...


class VideoEvidence(Protocol):
    def analyze(
        self,
        preview: Path,
        out: Path,
        changed_ranges: tuple[tuple[float, float], ...],
    ): ...


@dataclass(frozen=True)
class ControllerDeps:
    sessions: "SessionRepository"
    fcp: Any
    timeline: TimelineEngine
    watch: VideoEvidence


@dataclass(frozen=True)
class SessionHandle:
    id: str
    root: Path
    state: SessionState
    identity: ProjectIdentity | None
    analysis: dict[str, Any]
    pass_count: int


@dataclass(frozen=True)
class Candidate:
    number: int
    project_name: str
    fcpxml_path: Path
    preview_path: Path
    evidence_manifest: Path
    required_checks: dict[str, bool]
    observations: tuple[str, ...]
    score: float | None

    @property
    def verified(self) -> bool:
        return bool(self.required_checks) and all(self.required_checks.values())


@dataclass(frozen=True)
class SessionResult:
    id: str
    state: SessionState
    passes: int
    best_pass: Candidate | None
    failed_checks: tuple[str, ...]


class SessionRepository:
    def __init__(self, config: ControllerConfig):
        self.config = config
        self.root = config.session_root.expanduser().resolve()

    def create(self, request: EditRequest) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        paths = SessionPaths.create(self.root, session_id)
        record: dict[str, Any] = {
            "id": session_id,
            "state": SessionState.IDLE.value,
            "request": asdict(request),
            "pass_count": 0,
            "identity": None,
            "capture": None,
            "analysis": {},
            "candidates": [],
        }
        SessionStore(paths.root).save_state(record)
        return record

    def paths(self, session_id: str) -> SessionPaths:
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise SessionError("Invalid session id")
        root = (self.root / session_id).resolve()
        if not root.is_relative_to(self.root) or not root.is_dir():
            raise SessionError(f"Edit session does not exist: {session_id}")
        return SessionPaths.create(self.root, session_id)

    def store(self, session_id: str) -> SessionStore:
        return SessionStore(self.paths(session_id).root)

    def load(self, session_id: str) -> dict[str, Any]:
        record = self.store(session_id).load_state()
        if record.get("id") != session_id:
            raise SessionError(f"Edit session state is invalid: {session_id}")
        return record

    def save(self, record: dict[str, Any]) -> None:
        self.store(record["id"]).save_state(record)


def select_best_pass(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the highest-scoring reviewed pass, preferring the earlier tie."""
    reviewed = (
        candidate for candidate in candidates if candidate["score"] is not None
    )
    try:
        return min(
            reviewed,
            key=lambda candidate: (-candidate["score"], candidate["number"]),
        )
    except ValueError:
        raise ValueError("no reviewed candidates") from None


class EditSessionController:
    def __init__(self, deps: ControllerDeps):
        self.deps = deps

    async def start(self, request: EditRequest) -> SessionHandle:
        record = self.deps.sessions.create(request)
        session_id = record["id"]
        paths = self.deps.sessions.paths(session_id)
        store = self.deps.sessions.store(session_id)

        self._transition(record, SessionState.CAPTURE)
        capture = await capture_active_project(self.deps.fcp, paths, store)
        record["identity"] = asdict(capture.identity)
        record["capture"] = {
            "source_xml": str(capture.source_xml),
            "source_sha256": capture.source_sha256,
            "preserved_name": capture.preserved_name,
        }
        self._transition(record, SessionState.PRESERVE)
        self._transition(record, SessionState.ANALYZE)
        record["analysis"] = await self.deps.timeline.analyze(capture.source_xml)
        self._transition(record, SessionState.APPLY)
        return self._handle(record)

    async def apply(self, session_id: str, program: EditProgram) -> Candidate:
        record = self.deps.sessions.load(session_id)
        if record["pass_count"] >= self.deps.sessions.config.max_passes:
            raise SessionError("This edit session already used all three passes")
        state = SessionState(record["state"])
        if state not in {SessionState.APPLY, SessionState.CORRECT}:
            raise SessionError(f"Session cannot accept an edit while {state.value}")
        if self.deps.sessions.store(session_id).pending_actions():
            raise SessionError("A pending Final Cut action requires reconciliation")

        self._require_original(record)
        program.validate_for(record["analysis"])
        paths = self.deps.sessions.paths(session_id)
        number = int(record["pass_count"]) + 1
        identity = ProjectIdentity(**record["identity"])
        project_name = f"{identity.project} - AI Pass {number}"
        source = (
            Path(record["candidates"][-1]["fcpxml_path"])
            if record["candidates"]
            else Path(record["capture"]["source_xml"])
        )
        destination = paths.candidates / f"pass-{number:02d}.fcpxml"

        self._transition(record, SessionState.APPLY)
        written = (await self.deps.timeline.apply(source, program, destination)).resolve()
        if written != destination.resolve() or not written.is_file():
            raise SessionError("Timeline engine wrote outside the candidate path")

        self._transition(record, SessionState.IMPORT)
        await self._external(
            session_id,
            "finalcut.import",
            {"path": str(written), "project_name": project_name},
            self.deps.fcp.import_project(written, project_name),
        )

        self._transition(record, SessionState.PREVIEW)
        preview = paths.previews / f"pass-{number:02d}.mp4"
        await self._external(
            session_id,
            "finalcut.preview",
            {"project_name": project_name, "destination": str(preview)},
            self.deps.fcp.render_preview(project_name, preview),
        )
        if not preview.is_file():
            raise SessionError("Final Cut did not create the requested preview")

        self._transition(record, SessionState.VERIFY)
        evidence = await asyncio.to_thread(
            self.deps.watch.analyze,
            preview,
            paths.evidence / f"pass-{number:02d}",
            program.changed_ranges,
        )
        manifest = Path(evidence.manifest).resolve()
        if not manifest.is_file() or not manifest.is_relative_to(paths.evidence):
            raise SessionError("Watch evidence is outside the session")
        self._require_original(record)

        raw_candidate = {
            "number": number,
            "project_name": project_name,
            "fcpxml_path": str(written),
            "preview_path": str(preview),
            "evidence_manifest": str(manifest),
            "required_checks": {},
            "observations": [],
            "score": None,
        }
        record["candidates"].append(raw_candidate)
        record["pass_count"] = number
        self._transition(record, SessionState.VERIFY)
        return self._candidate(raw_candidate)

    async def record_review(
        self, session_id: str, pass_number: int, report: ReviewReport
    ) -> SessionResult:
        record = self.deps.sessions.load(session_id)
        if SessionState(record["state"]) is not SessionState.VERIFY:
            raise SessionError("Session is not awaiting a rendered review")
        if not report.required:
            raise SessionError("A review must include required checks")
        candidate = record["candidates"][-1]
        if candidate["number"] != pass_number:
            raise SessionError("Review pass does not match the current candidate")

        candidate["required_checks"] = dict(report.required)
        candidate["observations"] = list(report.observations)
        candidate["score"] = report.score
        self.deps.sessions.save(record)

        if report.verified:
            await self._open_project(record, candidate)
            self._transition(record, SessionState.READY)
            return self._result(record, candidate)
        if record["pass_count"] < self.deps.sessions.config.max_passes:
            self._transition(record, SessionState.CORRECT)
            return self._result(record, candidate)

        best = select_best_pass(record["candidates"])
        await self._open_project(record, best)
        self._transition(record, SessionState.BLOCKED)
        return self._result(record, best)

    def status(self, session_id: str) -> SessionResult:
        record = self.deps.sessions.load(session_id)
        reviewed = [item for item in record["candidates"] if item["score"] is not None]
        best = select_best_pass(reviewed) if reviewed else None
        return self._result(record, best)

    async def _open_project(
        self, record: dict[str, Any], candidate: dict[str, Any]
    ) -> None:
        await self._external(
            record["id"],
            "finalcut.open_project",
            {"project_name": candidate["project_name"]},
            self.deps.fcp.open_project(candidate["project_name"]),
        )

    async def _external(
        self,
        session_id: str,
        action: str,
        arguments: dict[str, Any],
        operation,
    ) -> None:
        store = self.deps.sessions.store(session_id)
        token = store.begin_external_action(action, arguments)
        await operation
        store.complete_external_action(token)

    def _transition(
        self, record: dict[str, Any], state: SessionState
    ) -> None:
        record["state"] = state.value
        self.deps.sessions.save(record)
        self.deps.sessions.store(record["id"]).append(
            "state_changed", {"state": state.value}
        )

    @staticmethod
    def _candidate(value: dict[str, Any]) -> Candidate:
        return Candidate(
            number=int(value["number"]),
            project_name=value["project_name"],
            fcpxml_path=Path(value["fcpxml_path"]),
            preview_path=Path(value["preview_path"]),
            evidence_manifest=Path(value["evidence_manifest"]),
            required_checks=dict(value["required_checks"]),
            observations=tuple(value["observations"]),
            score=value["score"],
        )

    def _handle(self, record: dict[str, Any]) -> SessionHandle:
        return SessionHandle(
            id=record["id"],
            root=self.deps.sessions.paths(record["id"]).root,
            state=SessionState(record["state"]),
            identity=(
                ProjectIdentity(**record["identity"]) if record["identity"] else None
            ),
            analysis=dict(record["analysis"]),
            pass_count=int(record["pass_count"]),
        )

    def _result(
        self, record: dict[str, Any], best: dict[str, Any] | None
    ) -> SessionResult:
        candidate = self._candidate(best) if best is not None else None
        failed = (
            tuple(key for key, value in candidate.required_checks.items() if not value)
            if candidate is not None
            else ()
        )
        return SessionResult(
            id=record["id"],
            state=SessionState(record["state"]),
            passes=int(record["pass_count"]),
            best_pass=candidate,
            failed_checks=failed,
        )

    @staticmethod
    def _require_original(record: dict[str, Any]) -> None:
        source = Path(record["capture"]["source_xml"])
        if not source.is_file() or file_sha256(source) != record["capture"]["source_sha256"]:
            raise SessionError("The preserved source XML changed during this edit session")
