"""Persisted three-pass controller for source-preserving Final Cut edits."""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from fcpxml.safe_xml import safe_parse

from editor_cli.config import ControllerConfig
from editor_cli.session.capture import (
    capture_active_project,
    file_sha256,
    recover_active_project_capture,
)
from editor_cli.session.locking import SessionLock
from editor_cli.session.models import (
    EditProgram,
    EditRequest,
    EvidenceBinding,
    ProjectIdentity,
    SessionState,
    required_checks_for_operations,
)
from editor_cli.session.paths import SessionPaths
from editor_cli.session.reconcile import (
    ReconciliationError,
    reconcile_external_action,
)
from editor_cli.session.store import SessionStore
from editor_cli.verification.review import ReviewReport
from editor_cli.verification.technical import (
    CandidateFCPXMLInspection,
    inspect_preview,
)


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
    sessions: SessionRepository
    fcp: Any
    timeline: TimelineEngine
    watch: VideoEvidence
    candidate_validator: Callable[[Path], Awaitable[CandidateFCPXMLInspection]]
    preview_inspector: Callable[..., ReviewReport] | None = None


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
    binding: EvidenceBinding | None = None
    required_check_names: tuple[str, ...] = ()
    duration_seconds: float | None = None
    media_references: tuple[Path, ...] = ()

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


@dataclass(frozen=True)
class UndoResult:
    number: int
    project_name: str
    fcpxml_path: Path
    source_version: int


class SessionRepository:
    def __init__(self, config: ControllerConfig):
        self.config = config
        self.root = config.session_root.expanduser().resolve()

    def create(self, request: EditRequest) -> dict[str, Any]:
        required_checks = required_checks_for_operations(request.required_operations)
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
            "required_checks": list(required_checks),
            "candidates": [],
        }
        return SessionStore(paths.root).save_state(record)

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
        expected_version = record.get("version")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise SessionError("Edit session state has no valid version")
        saved = self.store(record["id"]).save_state(
            record, expected_version=expected_version
        )
        record.clear()
        record.update(saved)


def select_best_pass(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the highest-scoring reviewed pass, preferring the earlier tie."""
    reviewed = (candidate for candidate in candidates if candidate["score"] is not None)
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
        with self._session_lock(session_id):
            return await self._start_locked(record)

    async def _start_locked(self, record: dict[str, Any]) -> SessionHandle:
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
            "media_references": [str(path) for path in capture.media_references],
        }
        self._transition(record, SessionState.PRESERVE)
        self._transition(record, SessionState.ANALYZE)
        record["analysis"] = await self.deps.timeline.analyze(capture.source_xml)
        self._transition(record, SessionState.APPLY)
        return self._handle(record)

    async def apply(self, session_id: str, program: EditProgram) -> Candidate:
        with self._session_lock(session_id):
            return await self._apply_locked(session_id, program)

    async def _apply_locked(self, session_id: str, program: EditProgram) -> Candidate:
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
        project_name = f"{identity.project} - {session_id[:8]} - AI Pass {number}"
        source = (
            Path(record["candidates"][-1]["fcpxml_path"])
            if record["candidates"]
            else Path(record["capture"]["source_xml"])
        )
        destination = paths.candidates / f"pass-{number:02d}.fcpxml"

        self._transition(record, SessionState.APPLY)
        written = (
            await self.deps.timeline.apply(source, program, destination)
        ).resolve()
        if written != destination.resolve() or not written.is_file():
            raise SessionError("Timeline engine wrote outside the candidate path")

        candidate_sha256 = file_sha256(written)
        candidate_qc = await self.deps.candidate_validator(written)
        self._require_artifact_hash(
            written,
            candidate_sha256,
            "Candidate XML changed during inspection",
        )
        if not candidate_qc.required.get("fcpxml_parseable", False):
            raise SessionError(
                "Candidate XML is malformed or does not contain one timeline"
            )
        if not candidate_qc.required.get("media_online", False):
            details = "; ".join(candidate_qc.observations)
            raise SessionError(f"Candidate XML references missing media: {details}")
        if not candidate_qc.required.get("timeline_valid", False):
            raise SessionError("Candidate XML failed timeline validation")
        if candidate_qc.duration_seconds is None:
            raise SessionError("Candidate XML has no usable duration")
        candidate_identity = ProjectIdentity(
            library=identity.library,
            event=identity.event,
            project=project_name,
            duration_seconds=candidate_qc.duration_seconds,
        )
        self._transition(record, SessionState.IMPORT)
        await self._external(
            session_id,
            "finalcut.import_xml",
            {"path": str(written), "identity": asdict(candidate_identity)},
            self.deps.fcp.import_project(written, candidate_identity),
            expected_identity=candidate_identity,
            idempotency={
                "candidate_sha256": candidate_sha256,
                "project_name": project_name,
            },
        )

        self._transition(record, SessionState.PREVIEW)
        preview = paths.previews / f"pass-{number:02d}.mp4"
        await self._external(
            session_id,
            "finalcut.share_preview",
            {"identity": asdict(candidate_identity), "destination": str(preview)},
            self.deps.fcp.render_preview(candidate_identity, preview),
            expected_identity=candidate_identity,
            idempotency={
                "candidate_sha256": candidate_sha256,
                "destination": str(preview),
            },
        )
        if not preview.is_file():
            raise SessionError("Final Cut did not create the requested preview")

        preview_sha256 = file_sha256(preview)
        preview_inspector = self.deps.preview_inspector or inspect_preview
        preview_qc = await asyncio.to_thread(
            preview_inspector,
            preview,
            expected_duration=candidate_qc.duration_seconds,
            fcpxml_qc=candidate_qc.verified,
        )
        self._require_artifact_hash(
            preview,
            preview_sha256,
            "Preview changed during inspection",
        )

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
        manifest_sha256 = file_sha256(manifest)
        frame_timestamps = self._validate_evidence_manifest(
            manifest, preview, preview_sha256, paths.evidence
        )
        self._require_artifact_hash(
            manifest,
            manifest_sha256,
            "Watch evidence manifest changed during inspection",
        )
        binding = EvidenceBinding(
            session_id=session_id,
            pass_number=number,
            state_version=int(record["version"]) + 1,
            project_name=project_name,
            candidate_sha256=candidate_sha256,
            preview_sha256=preview_sha256,
            manifest_sha256=manifest_sha256,
            frame_timestamps=frame_timestamps,
        )
        controller_checks = {
            "source_unchanged": True,
            "candidate_xml_valid": candidate_qc.verified,
            "preview_rendered": preview_qc.verified,
            "preview_watched": True,
        }

        raw_candidate = {
            "number": number,
            "project_name": project_name,
            "fcpxml_path": str(written),
            "preview_path": str(preview),
            "evidence_manifest": str(manifest),
            "identity": asdict(candidate_identity),
            "duration_seconds": candidate_qc.duration_seconds,
            "media_references": [str(path) for path in candidate_qc.media_references],
            "binding": binding.to_dict(),
            "required_check_names": list(record["required_checks"]),
            "controller_checks": controller_checks,
            "technical_checks": dict(preview_qc.required),
            "required_checks": {},
            "observations": list(candidate_qc.observations + preview_qc.observations),
            "score": None,
        }
        record["candidates"].append(raw_candidate)
        record["pass_count"] = number
        self._transition(record, SessionState.VERIFY)
        return self._candidate(raw_candidate)

    async def record_review(
        self, session_id: str, pass_number: int, report: ReviewReport
    ) -> SessionResult:
        with self._session_lock(session_id):
            return await self._record_review_locked(session_id, pass_number, report)

    async def _record_review_locked(
        self, session_id: str, pass_number: int, report: ReviewReport
    ) -> SessionResult:
        record = self.deps.sessions.load(session_id)
        self._require_no_pending(session_id)
        if SessionState(record["state"]) is not SessionState.VERIFY:
            raise SessionError("Session is not awaiting a rendered review")
        if not report.required:
            raise SessionError("A review must include required checks")
        candidate = record["candidates"][-1]
        if candidate["number"] != pass_number:
            raise SessionError("Review pass does not match the current candidate")

        required_check_names = tuple(record["required_checks"])
        if set(report.required) != set(required_check_names):
            raise SessionError("Review must contain the exact required checks")
        if any(type(value) is not bool for value in report.required.values()):
            raise SessionError("Review check results must be strict booleans")
        expected_binding = EvidenceBinding.from_dict(candidate["binding"])
        self._require_review_binding(
            record, candidate, report.binding, expected_binding
        )
        self._require_original(record)

        accepted_checks = dict(report.required)
        accepted_checks.update(candidate["controller_checks"])
        accepted_report = ReviewReport(
            required=accepted_checks,
            observations=tuple(report.observations),
            changed_ranges=tuple(report.changed_ranges),
            binding=expected_binding,
        )

        candidate["required_checks"] = dict(accepted_report.required)
        candidate["observations"] = list(candidate["observations"]) + list(
            accepted_report.observations
        )
        candidate["score"] = accepted_report.score
        self.deps.sessions.save(record)

        if accepted_report.verified:
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

    async def resume(self, session_id: str) -> SessionHandle:
        with self._session_lock(session_id):
            return await self._resume_locked(session_id)

    async def _resume_locked(self, session_id: str) -> SessionHandle:
        record = self.deps.sessions.load(session_id)
        store = self.deps.sessions.store(session_id)
        pending = store.pending_actions()
        reconciled_actions: list[str] = []
        if len(pending) > 1:
            raise SessionError(
                "Edit session journal contains multiple pending external actions"
            )
        if pending:
            for action in pending:
                try:
                    result = await reconcile_external_action(
                        action,
                        self.deps.fcp,
                        self.deps.sessions.paths(session_id).root,
                    )
                except ReconciliationError as exc:
                    raise SessionError(
                        f"Pending Final Cut action could not be reconciled: {action.action}"
                    ) from exc
                store.complete_external_action(action.token, result)
                reconciled_actions.append(action.action)

        if record.get("identity") is None:
            if SessionState(record["state"]) is not SessionState.CAPTURE:
                raise SessionError("Edit session has no captured project identity")
            capture = await recover_active_project_capture(
                self.deps.fcp, self.deps.sessions.paths(session_id), store
            )
            record["identity"] = asdict(capture.identity)
            record["capture"] = {
                "source_xml": str(capture.source_xml),
                "source_sha256": capture.source_sha256,
                "preserved_name": capture.preserved_name,
                "media_references": [str(path) for path in capture.media_references],
            }
            self._transition(record, SessionState.PRESERVE)
            self._transition(record, SessionState.ANALYZE)
            record["analysis"] = await self.deps.timeline.analyze(capture.source_xml)
            self._transition(record, SessionState.APPLY)
            return self._handle(record)

        if any(self._action_kind(name) == "import_xml" for name in reconciled_actions):
            self._transition(record, SessionState.PREVIEW)
        elif any(
            self._action_kind(name) == "open_project" for name in reconciled_actions
        ):
            candidate = record["candidates"][-1] if record["candidates"] else None
            if (
                candidate
                and candidate.get("required_checks")
                and all(candidate["required_checks"].values())
            ):
                self._transition(record, SessionState.READY)
            elif record["pass_count"] >= self.deps.sessions.config.max_passes:
                self._transition(record, SessionState.BLOCKED)

        active = tuple(await self.deps.fcp.active_projects())
        expected = self._latest_active_identity(store, record)
        if len(active) != 1 or active[0] != expected:
            raise SessionError(
                "The active project changed; reopen the captured Final Cut project"
            )
        self._require_original(record)
        return self._handle(record)

    async def undo(self, session_id: str) -> UndoResult:
        with self._session_lock(session_id):
            return await self._undo_locked(session_id)

    async def _undo_locked(self, session_id: str) -> UndoResult:
        record = self.deps.sessions.load(session_id)
        self._require_no_pending(session_id)
        if not record["candidates"]:
            raise SessionError("Undo requires a candidate version")
        if len(record["candidates"]) > 1:
            previous = record["candidates"][-2]
            source_path = Path(previous["fcpxml_path"])
            source_version = int(previous["number"])
            duration = float(previous["duration_seconds"])
        else:
            source_path = Path(record["capture"]["source_xml"])
            source_version = 0
            duration = float(record["identity"]["duration_seconds"])
        source_identity = ProjectIdentity(**record["identity"])
        undo_count = int(record.get("undo_count", 0)) + 1
        identity = ProjectIdentity(
            source_identity.library,
            source_identity.event,
            f"{source_identity.project} - {session_id[:8]} - Undo {undo_count}",
            duration,
        )
        path = (
            self.deps.sessions.paths(session_id).candidates
            / f"undo-{undo_count:02d}.fcpxml"
        )
        self._write_undo_candidate(source_path, path, identity.project)
        candidate_sha256 = file_sha256(path)
        inspection = await self.deps.candidate_validator(path)
        self._require_artifact_hash(
            path, candidate_sha256, "Undo candidate changed during inspection"
        )
        if (
            not inspection.verified
            or inspection.duration_seconds != identity.duration_seconds
        ):
            raise SessionError("Undo candidate failed exact timeline validation")
        imported = await self._external(
            session_id,
            "finalcut.import_xml",
            {"path": str(path), "identity": asdict(identity)},
            self.deps.fcp.import_project(path, identity),
            expected_identity=identity,
            idempotency={
                "candidate_sha256": candidate_sha256,
                "project_name": identity.project,
            },
        )
        if imported != identity:
            raise SessionError("Final Cut did not import the exact undo project")
        opened = await self._external(
            session_id,
            "finalcut.open_project",
            {"identity": asdict(identity)},
            self.deps.fcp.open_project(identity),
            expected_identity=identity,
            idempotency={"project_name": identity.project},
        )
        if opened != identity:
            raise SessionError("Final Cut did not open the exact undo project")
        record["undo_count"] = undo_count
        record.setdefault("undo_versions", []).append(
            {
                "number": undo_count,
                "project_name": identity.project,
                "fcpxml_path": str(path),
                "sha256": candidate_sha256,
                "identity": asdict(identity),
                "source_version": source_version,
            }
        )
        self.deps.sessions.save(record)
        self.deps.sessions.store(session_id).append(
            "undo_created",
            {"project_name": identity.project, "source_version": source_version},
        )
        return UndoResult(undo_count, identity.project, path, source_version)

    async def _open_project(
        self, record: dict[str, Any], candidate: dict[str, Any]
    ) -> None:
        await self._external(
            record["id"],
            "finalcut.open_project",
            {"identity": candidate["identity"]},
            self.deps.fcp.open_project(ProjectIdentity(**candidate["identity"])),
            expected_identity=ProjectIdentity(**candidate["identity"]),
            idempotency={"project_name": candidate["project_name"]},
        )

    async def _external(
        self,
        session_id: str,
        action: str,
        arguments: dict[str, Any],
        operation,
        *,
        expected_identity: ProjectIdentity,
        idempotency: dict[str, Any],
    ) -> Any:
        store = self.deps.sessions.store(session_id)
        token = store.begin_external_action(
            action,
            arguments,
            expected_identity=asdict(expected_identity),
            idempotency=idempotency,
        )
        result = await operation
        store.complete_external_action(token, self._external_result(result))
        return result

    def _session_lock(self, session_id: str) -> SessionLock:
        return SessionLock(
            self.deps.sessions.paths(session_id).root,
            blocking=False,
        )

    def _require_no_pending(self, session_id: str) -> None:
        if self.deps.sessions.store(session_id).pending_actions():
            raise SessionError("A pending Final Cut action requires reconciliation")

    @staticmethod
    def _action_kind(action: str) -> str:
        aliases = {
            "commandpost.export": "export_xml",
            "finalcut.export": "export_xml",
            "finalcut.export_xml": "export_xml",
            "commandpost.duplicate": "duplicate_project",
            "finalcut.duplicate": "duplicate_project",
            "finalcut.duplicate_project": "duplicate_project",
            "finalcut.import_xml": "import_xml",
            "finalcut.share_preview": "share_preview",
            "finalcut.open_project": "open_project",
            "internet.download": "download",
        }
        return aliases.get(action, action)

    @classmethod
    def _latest_active_identity(
        cls, store: SessionStore, record: dict[str, Any]
    ) -> ProjectIdentity:
        for event in reversed(store.events()):
            if event.get("kind") != "external_action":
                continue
            data = event.get("data")
            if not isinstance(data, dict) or data.get("status") != "complete":
                continue
            if cls._action_kind(str(data.get("action"))) not in {
                "export_xml",
                "duplicate_project",
                "import_xml",
                "share_preview",
                "open_project",
            }:
                continue
            try:
                expected = data["expected"]
                if not isinstance(expected, dict) or set(expected) != {
                    "identity",
                    "idempotency",
                }:
                    raise ValueError
                identity = expected["identity"]
                if not isinstance(identity, dict) or set(identity) != {
                    "library",
                    "event",
                    "project",
                    "duration_seconds",
                }:
                    raise ValueError
                return ProjectIdentity(**identity)
            except (KeyError, TypeError, ValueError) as exc:
                raise SessionError(
                    "Completed Final Cut action has malformed identity evidence"
                ) from exc
        return ProjectIdentity(**record["identity"])

    @staticmethod
    def _write_undo_candidate(source: Path, destination: Path, name: str) -> None:
        try:
            if destination.exists():
                raise SessionError("Undo candidate path already exists")
            tree = safe_parse(str(source))
            projects = tree.getroot().findall(".//project")
            if len(projects) != 1:
                raise SessionError("Undo source must contain one Final Cut project")
            projects[0].set("name", name)
            tree.write(
                str(destination),
                encoding="utf-8",
                xml_declaration=True,
            )
        except SessionError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise SessionError("Could not create the undo candidate") from exc

    @staticmethod
    def _validate_evidence_manifest(
        manifest: Path,
        preview: Path,
        preview_sha256: str,
        evidence_root: Path,
    ) -> tuple[float, ...]:
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
            preview_data = value["preview"]
            if (
                not isinstance(value, dict)
                or not isinstance(preview_data, dict)
                or Path(preview_data["path"]).expanduser().resolve() != preview
                or preview_data["sha256"] != preview_sha256
            ):
                raise ValueError
            frames = value["frames"]
            if not isinstance(frames, list) or not frames:
                raise ValueError
            timestamps: set[float] = set()
            for frame in frames:
                if not isinstance(frame, dict):
                    raise TypeError
                frame_path = Path(frame["path"]).expanduser().resolve()
                timestamp = frame["timestamp_seconds"]
                if (
                    not frame_path.is_file()
                    or not frame_path.is_relative_to(evidence_root)
                    or isinstance(timestamp, bool)
                    or not isinstance(timestamp, (int, float))
                    or not math.isfinite(timestamp)
                    or timestamp < 0
                ):
                    raise ValueError
                timestamps.add(float(timestamp))
            if not timestamps:
                raise ValueError
            return tuple(sorted(timestamps))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SessionError(
                "Watch evidence manifest does not bind the rendered preview"
            ) from exc

    @staticmethod
    def _require_review_binding(
        record: dict[str, Any],
        candidate: dict[str, Any],
        supplied: EvidenceBinding | None,
        expected: EvidenceBinding,
    ) -> None:
        if supplied is None:
            raise SessionError("Review requires the candidate evidence binding")
        if supplied.preview_sha256 != expected.preview_sha256:
            raise SessionError("Review preview hash does not match the candidate")
        if supplied.candidate_sha256 != expected.candidate_sha256:
            raise SessionError("Review candidate hash does not match the candidate")
        if supplied.manifest_sha256 != expected.manifest_sha256:
            raise SessionError("Review manifest hash does not match the evidence")
        if supplied != expected:
            raise SessionError("Review binding does not match the current candidate")
        if int(record.get("version", -1)) != expected.state_version:
            raise SessionError("Review state version is stale")
        EditSessionController._require_artifact_hash(
            Path(candidate["fcpxml_path"]),
            expected.candidate_sha256,
            "Candidate XML hash changed after inspection",
        )
        EditSessionController._require_artifact_hash(
            Path(candidate["preview_path"]),
            expected.preview_sha256,
            "Review preview hash changed after inspection",
        )
        EditSessionController._require_artifact_hash(
            Path(candidate["evidence_manifest"]),
            expected.manifest_sha256,
            "Review manifest hash changed after inspection",
        )

    @staticmethod
    def _require_artifact_hash(path: Path, expected: str, message: str) -> None:
        try:
            actual = file_sha256(path)
        except OSError as exc:
            raise SessionError(message) from exc
        if actual != expected:
            raise SessionError(message)

    @classmethod
    def _external_result(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, ProjectIdentity):
            return {"identity": asdict(value)}
        if isinstance(value, dict):
            return cls._json_value(value)
        if hasattr(value, "kind") and hasattr(value, "output"):
            result: dict[str, Any] = {
                "kind": value.kind,
                "output": str(value.output),
            }
            project = getattr(value, "project", None)
            if isinstance(project, ProjectIdentity):
                result["identity"] = asdict(project)
            return result
        return {"result_type": type(value).__name__}

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, ProjectIdentity):
            return asdict(value)
        if isinstance(value, dict):
            return {key: cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def _transition(self, record: dict[str, Any], state: SessionState) -> None:
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
            binding=(
                EvidenceBinding.from_dict(value["binding"])
                if value.get("binding") is not None
                else None
            ),
            required_check_names=tuple(value.get("required_check_names", ())),
            duration_seconds=value.get("duration_seconds"),
            media_references=tuple(
                Path(path) for path in value.get("media_references", ())
            ),
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
        if (
            not source.is_file()
            or file_sha256(source) != record["capture"]["source_sha256"]
        ):
            raise SessionError(
                "The preserved source XML changed during this edit session"
            )
