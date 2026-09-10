"""Concrete service groups shared by the CLI and MCP surfaces."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from editor_cli.acquire.internet import InternetAcquirer
from editor_cli.adapters.fcpxml_mcp import FCPXMLMCPClient
from editor_cli.adapters.final_cut_control import FinalCutControl
from editor_cli.adapters.native_final_cut import NativeFinalCutClient
from editor_cli.adapters.timeline_engine import FCPXMLTimelineEngine
from editor_cli.adapters.fcp_assets import InstalledAssetCatalog
from editor_cli.adapters.watch import WatchAdapter
from editor_cli.config import ControllerConfig, load_controller_config
from editor_cli.direct.media import LOCAL_INPUT
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
    required_checks_for_operations,
)
from editor_cli.session.locking import SessionLock
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
            if (
                not isinstance(required_operations, (list, tuple))
                or not required_operations
            ):
                raise ValueError(
                    "editor_session start requires non-empty required_operations"
                )
            request = EditRequest(
                prompt or "", required_operations=tuple(required_operations)
            )
            required_checks_for_operations(request.required_operations)
            if not self.doctor().get("ready", False):
                raise RuntimeError(
                    "Run editor-cli doctor and resolve failed checks first"
                )
            if session_id is not None:
                raise ValueError("A new session cannot reuse a session ID")
            return _handle(await self.controller.start(request))
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
    def __init__(self, controller, sessions, fcpxml, *, asset_catalog=None):
        self.controller = controller
        self.sessions = sessions
        self.fcpxml = fcpxml
        self.asset_catalog = asset_catalog

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

            def require_string_keys(value: Any, location: str) -> None:
                if isinstance(value, dict):
                    if any(not isinstance(key, str) for key in value):
                        raise ValueError(f"{location} keys must be strings")
                    for key, nested in value.items():
                        require_string_keys(nested, f"{location}.{key}")
                elif isinstance(value, list):
                    for index, nested in enumerate(value):
                        require_string_keys(nested, f"{location}[{index}]")

            require_string_keys(edit_program, "edit_program")
            unexpected = set(edit_program) - {"operations", "changed_ranges"}
            if unexpected:
                raise ValueError(
                    f"edit_program has unexpected fields: {sorted(unexpected)}"
                )
            raw_operations = edit_program.get("operations")
            if not isinstance(raw_operations, list):
                raise ValueError("edit_program.operations must be a list")
            for item in raw_operations:
                if not isinstance(item, dict):
                    raise TypeError("Each edit operation must be an object")
                unexpected = set(item) - {"group", "action", "arguments"}
                if unexpected:
                    raise ValueError(
                        f"edit operation has unexpected fields: {sorted(unexpected)}"
                    )
                if not isinstance(item.get("arguments", {}), dict):
                    raise TypeError("Edit operation arguments must be an object")
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
            if self.sessions is not None:
                record = self.sessions.load(session_id)
                source = Path(
                    record.get("working_xml")
                    or (
                        record["candidates"][-1]["fcpxml_path"]
                        if record.get("candidates")
                        else record["capture"]["source_xml"]
                    )
                )
                import xml.etree.ElementTree as ET

                known_ids = {
                    node.get("id") for node in ET.parse(source).findall(".//asset")
                }
                for operation in program.operations:
                    args = operation.arguments
                    ids = [args.get("asset_id")]
                    if operation.action == "apply_template":
                        ids.extend(
                            item.get("asset_id")
                            for item in args.get("clips", {}).values()
                        )
                    unknown_ids = [
                        item for item in ids if item and item not in known_ids
                    ]
                    if unknown_ids:
                        raise PermissionError(
                            f"Unknown session asset_id: {unknown_ids[0]}"
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
            result = {
                "session_id": session_id,
                "state": record["state"],
                "analysis": record["analysis"],
                "source_xml": record["capture"]["source_xml"],
                "candidates": record["candidates"],
            }
            if self.asset_catalog is not None:
                result["installed_assets"] = [
                    {
                        "kind": asset.kind,
                        "name": asset.name,
                        "category": asset.category,
                        "action_id": asset.action_id,
                        "handler": asset.handler,
                    }
                    for asset in self.asset_catalog.scan()
                ]
            else:
                result["installed_assets"] = []
            planning = record.get("analysis") or {}
            for key, default in {
                "clips": [],
                "gaps": [],
                "roles": [],
                "markers": [],
                "effects": [],
                "transcript": [],
                "pacing": {},
            }.items():
                result[key] = planning.get(key, default)
            return result
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
            undone = await self.controller.undo(session_id)
            return {
                "session_id": session_id,
                "project_name": undone.project_name,
                "fcpxml_path": str(undone.fcpxml_path),
            }
        raise ValueError(f"Unknown editor_timeline action: {action}")


class MediaService:
    def __init__(
        self,
        sessions: SessionRepository,
        *,
        timeline=None,
        probe_media: Callable[[Path], tuple[bool, bool]] | None = None,
    ):
        self.sessions = sessions
        self.timeline = timeline
        self.probe_media = probe_media or self._probe_media

    @staticmethod
    def _probe_media(path: Path) -> tuple[bool, bool]:
        completed = subprocess.run(
            (
                "ffprobe",
                "-v",
                "error",
                *LOCAL_INPUT,
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise ValueError("Registered asset is not readable media")
        try:
            streams = json.loads(completed.stdout)["streams"]
            kinds = {
                stream["codec_type"]
                for stream in streams
                if isinstance(stream, dict) and "codec_type" in stream
            }
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Registered asset has invalid media metadata") from exc
        has_video = "video" in kinds
        has_audio = "audio" in kinds
        if not has_video and not has_audio:
            raise ValueError("Registered asset has no audio or video streams")
        return has_video, has_audio

    async def dispatch(
        self,
        action: str,
        *,
        session_id: str,
        url: str | None,
        purpose: str | None,
        asset_path: str | None = None,
        name: str | None = None,
        duration_seconds: float | None = None,
        has_audio: bool | None = None,
    ) -> dict[str, Any]:
        paths = self.sessions.paths(session_id)
        store = self.sessions.store(session_id)
        acquirer = InternetAcquirer(paths.assets, store=store)
        if action == "acquire":
            if not url or not purpose:
                raise ValueError("Media acquisition requires url and purpose")
            with SessionLock(paths.root, blocking=False):
                record = self.sessions.load(session_id)
                if not record.get("request", {}).get("internet_media", True):
                    raise PermissionError(
                        "This edit session does not allow internet media"
                    )
                if store.pending_actions():
                    raise RuntimeError(
                        "A pending external action requires reconciliation"
                    )
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
        if action == "register":
            if self.timeline is None:
                raise RuntimeError("Timeline media registration is unavailable")
            if (
                not asset_path
                or not name
                or isinstance(duration_seconds, bool)
                or not isinstance(duration_seconds, (int, float))
            ):
                raise ValueError("Media registration requires complete asset metadata")
            media = paths.require_read(Path(asset_path))
            with SessionLock(paths.root, blocking=False):
                record = self.sessions.load(session_id)
                if record["state"] not in {"apply", "correct"}:
                    raise RuntimeError(
                        f"Session cannot register media while {record['state']}"
                    )
                if self.sessions.store(session_id).pending_actions():
                    raise RuntimeError(
                        "A pending Final Cut action requires reconciliation"
                    )
                if not media.is_file():
                    raise PermissionError("Registered asset is not a regular file")
                rows = []
                if acquirer.provenance_path.is_file():
                    rows = [
                        json.loads(line)
                        for line in acquirer.provenance_path.read_text(
                            encoding="utf-8"
                        ).splitlines()
                        if line.strip()
                    ]
                digest = sha256(media.read_bytes()).hexdigest()
                if not any(
                    row.get("path") == str(media) and row.get("sha256") == digest
                    for row in rows
                ):
                    raise PermissionError(
                        "Media does not match an acquired session asset"
                    )
                has_video, probed_has_audio = self.probe_media(media)
                source = Path(
                    record.get("working_xml")
                    or (
                        record["candidates"][-1]["fcpxml_path"]
                        if record.get("candidates")
                        else record["capture"]["source_xml"]
                    )
                )
                registered = list(record.get("registered_assets", []))
                destination = (
                    paths.candidates / f"register-{len(registered) + 1:02d}.fcpxml"
                )
                receipt = await self.timeline.register_media(
                    source,
                    media,
                    destination,
                    name=name,
                    duration_seconds=duration_seconds,
                    has_video=has_video,
                    has_audio=probed_has_audio,
                )
                if sha256(media.read_bytes()).hexdigest() != digest:
                    raise PermissionError("Media changed during registration")
                registered.append(
                    {
                        "asset_id": receipt.asset_id,
                        "path": str(media),
                        "sha256": digest,
                        "name": name,
                    }
                )
                record["registered_assets"] = registered
                record["working_xml"] = str(receipt.path)
                self.sessions.save(record)
            return {
                "asset_id": receipt.asset_id,
                "path": str(media),
                "working_xml": str(receipt.path),
            }
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
        timeline=TimelineService(
            controller, sessions, fcpxml, asset_catalog=InstalledAssetCatalog()
        ),
        media=MediaService(sessions, timeline=timeline),
        verify=VerifyService(controller, sessions, fcpxml),
    )
