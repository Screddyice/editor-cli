"""Concrete service groups shared by the CLI and MCP surfaces."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from editor_cli.acquire.internet import InternetAcquirer
from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.adapters.final_cut_control import FinalCutControl
from editor_cli.adapters.native_final_cut import NativeFinalCutClient
from editor_cli.adapters.timeline_engine import FCPXMLTimelineEngine
from editor_cli.adapters.watch import WatchAdapter
from editor_cli.config import ControllerConfig, load_controller_config
from editor_cli.session.controller import (
    Candidate,
    ControllerDeps,
    EditSessionController,
    SessionError,
    SessionHandle,
    SessionRepository,
    SessionResult,
)
from editor_cli.session.models import (
    EditOperation,
    EditProgram,
    EditRequest,
    EvidenceBinding,
    SessionState,
)
from editor_cli.verification.review import parse_creative_review
from editor_cli.verification.technical import (
    inspect_candidate_fcpxml,
    inspect_preview,
)


class ServiceError(ValueError):
    """Raised when a service request violates a controller-owned contract."""


class _LazyWatch:
    def __init__(self, script: Path):
        self.script = script
        self._adapter: WatchAdapter | None = None

    def analyze(self, *args: Any, **kwargs: Any):
        if self._adapter is None:
            self._adapter = WatchAdapter(self.script)
        return self._adapter.analyze(*args, **kwargs)


def _candidate(value: Candidate | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "number": value.number,
        "project_name": value.project_name,
        "fcpxml_path": str(value.fcpxml_path),
        "preview_path": str(value.preview_path),
        "evidence_manifest": str(value.evidence_manifest),
        "required_checks": dict(value.required_checks),
        "observations": list(value.observations),
        "score": value.score,
        "verified": value.verified,
        "binding": value.binding.to_dict() if value.binding is not None else None,
        "required_check_names": list(value.required_check_names),
        "duration_seconds": value.duration_seconds,
        "media_references": [str(path) for path in value.media_references],
    }


def _handle(value: SessionHandle) -> dict[str, Any]:
    return {
        "session_id": value.id,
        "root": str(value.root),
        "state": value.state.value,
        "pass_count": value.pass_count,
        "identity": (
            {
                "library": value.identity.library,
                "event": value.identity.event,
                "project": value.identity.project,
                "duration_seconds": value.identity.duration_seconds,
            }
            if value.identity
            else None
        ),
        "analysis": value.analysis,
        "best_candidate": None,
        "failed_checks": [],
    }


def _result(value: SessionResult) -> dict[str, Any]:
    return {
        "session_id": value.id,
        "state": value.state.value,
        "pass_count": value.passes,
        "best_candidate": _candidate(value.best_pass),
        "failed_checks": list(value.failed_checks),
    }


class SessionService:
    def __init__(
        self,
        controller: EditSessionController,
        *,
        doctor: Callable[[], dict[str, Any]],
    ):
        self.controller = controller
        self.doctor = doctor

    async def dispatch(
        self,
        action: str,
        *,
        prompt: str | None,
        session_id: str | None,
        required_operations: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if action == "doctor":
            return self.doctor()
        if action == "start":
            if not self.doctor().get("ready", False):
                raise RuntimeError(
                    "Run editor-cli doctor and resolve failed checks first"
                )
            if session_id is not None:
                raise ValueError("A new session cannot reuse a session ID")
            return _handle(
                await self.controller.start(
                    EditRequest(
                        prompt or "",
                        required_operations=tuple(required_operations or ()),
                    )
                )
            )
        if not session_id:
            raise ValueError(f"editor_session {action} requires session_id")
        if action == "status":
            return _result(self.controller.status(session_id))
        if action == "resume":
            return _handle(await self.controller.resume(session_id))
        if action == "finish":
            status = self.controller.status(session_id)
            if status.state not in {SessionState.READY, SessionState.BLOCKED}:
                raise RuntimeError(
                    "The edit loop must finish verification before handoff"
                )
            result = _result(status)
            result["final_export"] = "user"
            return result
        raise ValueError(f"Unknown editor_session action: {action}")


class TimelineService:
    def __init__(self, controller, sessions, fcpxml):
        self.controller = controller
        self.sessions = sessions
        self.fcpxml = fcpxml

    async def dispatch(
        self,
        action: str,
        *,
        session_id: str,
        edit_program: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if action == "apply":
            if not isinstance(edit_program, dict):
                raise ValueError("editor_timeline apply requires edit_program")
            raw_operations = edit_program.get("operations")
            if not isinstance(raw_operations, list):
                raise ValueError("edit_program.operations must be a list")
            program = EditProgram(
                operations=tuple(
                    EditOperation(
                        group=item["group"],
                        action=item["action"],
                        arguments=dict(item.get("arguments", {})),
                    )
                    for item in raw_operations
                ),
                changed_ranges=tuple(
                    (float(item[0]), float(item[1]))
                    for item in edit_program.get("changed_ranges", [])
                ),
            )
            return {
                "candidate": _candidate(
                    await self.controller.apply(session_id, program)
                )
            }
        if self.sessions is None:
            raise RuntimeError("Session repository is unavailable")
        record = self.sessions.load(session_id)
        if action == "inspect":
            return {
                "session_id": session_id,
                "state": record["state"],
                "analysis": record["analysis"],
                "source_xml": record["capture"]["source_xml"],
                "candidates": record["candidates"],
            }
        if action == "diff":
            if not record["candidates"]:
                raise RuntimeError("The session has no candidate to compare")
            return await self.fcpxml.call(
                "diagnose",
                {
                    "action": "diff_timelines",
                    "args": {
                        "filepath_a": record["capture"]["source_xml"],
                        "filepath_b": record["candidates"][-1]["fcpxml_path"],
                    },
                },
            )
        if action == "undo":
            candidates = record["candidates"]
            project = (
                candidates[-2]["project_name"]
                if len(candidates) > 1
                else record["identity"]["project"]
            )
            await self.controller.deps.fcp.open_project(project)
            return {"session_id": session_id, "opened_project": project}
        raise ValueError(f"Unknown editor_timeline action: {action}")


class MediaService:
    def __init__(self, sessions: SessionRepository):
        self.sessions = sessions

    async def dispatch(
        self,
        action: str,
        *,
        session_id: str,
        url: str | None,
        purpose: str | None,
    ) -> dict[str, Any]:
        paths = self.sessions.paths(session_id)
        acquirer = InternetAcquirer(paths.assets)
        if action == "acquire":
            if not url or not purpose:
                raise ValueError("Media acquisition requires url and purpose")
            asset = acquirer.acquire(url, purpose)
            return {
                "path": str(asset.path),
                "source_url": asset.source_url,
                "sha256": asset.sha256,
                "purpose": asset.purpose,
                "author": asset.author,
                "license_note": asset.license_note,
            }
        if action == "list":
            rows = []
            if acquirer.provenance_path.is_file():
                rows = [
                    json.loads(line)
                    for line in acquirer.provenance_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            return {"session_id": session_id, "assets": rows}
        raise ValueError(f"Unknown editor_media action: {action}")


class VerifyService:
    def __init__(self, controller, sessions, fcpxml):
        self.controller = controller
        self.sessions = sessions
        self.fcpxml = fcpxml

    def _candidate_record(self, session_id: str, pass_number: int | None):
        record = self.sessions.load(session_id)
        if not record["candidates"]:
            raise RuntimeError("The session has no rendered candidate")
        candidate = record["candidates"][-1]
        if pass_number is not None:
            matches = [
                item for item in record["candidates"] if item["number"] == pass_number
            ]
            if not matches:
                raise ValueError(f"Session has no pass {pass_number}")
            candidate = matches[0]
        return record, candidate

    async def dispatch(
        self,
        action: str,
        *,
        session_id: str,
        pass_number: int | None,
        report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record, candidate = self._candidate_record(session_id, pass_number)
        if action == "preview":
            technical = inspect_preview(
                Path(candidate["preview_path"]),
                expected_duration=float(candidate["duration_seconds"]),
                fcpxml_qc=bool(
                    candidate.get("controller_checks", {}).get(
                        "candidate_xml_valid", False
                    )
                ),
            )
            return {
                "candidate": candidate,
                "technical": {
                    "required": technical.required,
                    "observations": list(technical.observations),
                },
            }
        if action == "watch":
            path = Path(candidate["evidence_manifest"])
            return json.loads(path.read_text(encoding="utf-8"))
        if action == "compare":
            return await self.fcpxml.call(
                "diagnose",
                {
                    "action": "diff_timelines",
                    "args": {
                        "filepath_a": record["capture"]["source_xml"],
                        "filepath_b": candidate["fcpxml_path"],
                    },
                },
            )
        if action == "record":
            if not isinstance(report, dict):
                raise ValueError("Verification record requires a report object")
            try:
                expected_binding = EvidenceBinding.from_dict(candidate["binding"])
                creative = parse_creative_review(
                    json.dumps(report),
                    tuple(record["required_checks"]),
                    expected_binding=expected_binding,
                )
                result = await self.controller.record_review(
                    session_id, candidate["number"], creative
                )
            except (KeyError, SessionError, TypeError, ValueError) as exc:
                raise ServiceError(str(exc)) from exc
            return _result(result)
        raise ValueError(f"Unknown editor_verify action: {action}")


def build_services(
    config: ControllerConfig | None = None,
    *,
    doctor: Callable[[], dict[str, Any]],
):
    from editor_cli.mcp_server import ServiceRegistry

    config = config or load_controller_config()
    sessions = SessionRepository(config)
    fcpxml = FCPXMLMCPClient(
        config.fcpxml_command,
        journal_root=config.session_root / ".fcp-mcp-journal",
        allowed_roots=(config.session_root,),
    )
    native = NativeFinalCutClient(
        config.native_helper,
        action_timeout=config.native_action_timeout_seconds,
    )
    final_cut = FinalCutControl(
        native,
        fcpxml,
        session_root=config.session_root,
    )
    timeline = FCPXMLTimelineEngine(fcpxml)
    watch = _LazyWatch(Path("~/.codex/skills/watch/scripts/watch.py").expanduser())

    async def validate_candidate(path: Path):
        upstream = await fcpxml.call(
            "diagnose",
            {
                "action": "validate_timeline",
                "args": {"filepath": str(path)},
            },
        )
        return inspect_candidate_fcpxml(path, upstream_validation=upstream)

    controller = EditSessionController(
        deps=ControllerDeps(
            sessions=sessions,
            fcp=final_cut,
            timeline=timeline,
            watch=watch,
            candidate_validator=validate_candidate,
        )
    )
    return ServiceRegistry(
        session=SessionService(controller, doctor=doctor),
        timeline=TimelineService(controller, sessions, fcpxml),
        media=MediaService(sessions),
        verify=VerifyService(controller, sessions, fcpxml),
    )
