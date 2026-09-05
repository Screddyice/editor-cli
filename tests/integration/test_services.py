import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from editor_cli.config import ControllerConfig
from editor_cli.mcp_server import build_default_services
from editor_cli.services import (
    ServiceError,
    SessionService,
    TimelineService,
    VerifyService,
)
from editor_cli.session.controller import Candidate, SessionRepository, SessionResult
from editor_cli.session.models import EditRequest, EvidenceBinding, SessionState


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeController:
    def __init__(self):
        self.calls = []

    async def start(self, request):
        self.calls.append(("start", request.prompt, request.required_operations))
        return SimpleNamespace(
            id="a" * 32,
            state=SessionState.APPLY,
            pass_count=0,
            root=Path("/tmp/session"),
            identity=None,
            analysis={"duration_seconds": 12.0},
        )

    def status(self, session_id):
        self.calls.append(("status", session_id))
        return SessionResult(
            id=session_id,
            state=SessionState.BLOCKED,
            passes=3,
            best_pass=Candidate(
                number=2,
                project_name="Demo - AI Pass 2",
                fcpxml_path=Path("/tmp/pass-02.fcpxml"),
                preview_path=Path("/tmp/pass-02.mp4"),
                evidence_manifest=Path("/tmp/manifest.json"),
                required_checks={"meme_insert": False},
                observations=("missing reaction",),
                score=0.5,
            ),
            failed_checks=("meme_insert",),
        )

    async def resume(self, session_id):
        self.calls.append(("resume", session_id))
        return SimpleNamespace(
            id=session_id,
            state=SessionState.CORRECT,
            pass_count=1,
            root=Path("/tmp/session"),
            identity=None,
            analysis={"duration_seconds": 12.0},
        )


class FakeTimelineController:
    def __init__(self):
        self.undo_calls = []

    async def apply(self, session_id, program):
        assert session_id == "d" * 32
        assert program.operations[0].action == "fill_gaps"
        return Candidate(
            number=1,
            project_name="Demo - AI Pass 1",
            fcpxml_path=Path("/tmp/pass-01.fcpxml"),
            preview_path=Path("/tmp/pass-01.mp4"),
            evidence_manifest=Path("/tmp/manifest.json"),
            required_checks={},
            observations=(),
            score=None,
        )

    async def undo(self, session_id):
        self.undo_calls.append(session_id)
        return SimpleNamespace(
            project_name="Demo - Undo 1",
            fcpxml_path=Path("/tmp/undo-01.fcpxml"),
        )


@pytest.mark.anyio
async def test_session_service_serializes_start_status_and_resume():
    controller = FakeController()
    service = SessionService(controller, doctor=lambda: {"ready": True})

    started = await service.dispatch(
        "start",
        prompt="remove gaps",
        session_id=None,
        required_operations=["remove_gaps"],
    )
    status = await service.dispatch("status", prompt=None, session_id="b" * 32)
    resumed = await service.dispatch("resume", prompt=None, session_id="c" * 32)

    assert started["session_id"] == "a" * 32
    assert started["state"] == "apply"
    assert status["best_candidate"]["number"] == 2
    assert status["failed_checks"] == ["meme_insert"]
    assert resumed["state"] == "correct"


@pytest.mark.anyio
async def test_session_service_persists_controller_owned_required_checks():
    controller = FakeController()
    service = SessionService(controller, doctor=lambda: {"ready": True})

    await service.dispatch(
        "start",
        prompt="remove gaps",
        session_id=None,
        required_operations=["gap_removed"],
    )

    assert controller.calls == [("start", "remove gaps", ("gap_removed",))]


@pytest.mark.anyio
@pytest.mark.parametrize("required_operations", [None, []])
async def test_session_service_refuses_start_without_required_operations(
    required_operations,
):
    controller = FakeController()
    service = SessionService(controller, doctor=lambda: {"ready": True})

    with pytest.raises(ValueError, match="required_operations"):
        await service.dispatch(
            "start",
            prompt="remove gaps",
            session_id=None,
            required_operations=required_operations,
        )

    assert controller.calls == []


@pytest.mark.anyio
async def test_session_service_refuses_unknown_required_operation_before_start():
    controller = FakeController()
    service = SessionService(controller, doctor=lambda: {"ready": True})

    with pytest.raises(ValueError, match="Unsupported required edit operation"):
        await service.dispatch(
            "start",
            prompt="make an unsupported edit",
            session_id=None,
            required_operations=["unknown_operation"],
        )

    assert controller.calls == []


@pytest.mark.anyio
async def test_session_service_refuses_start_when_device_is_not_ready():
    service = SessionService(
        FakeController(),
        doctor=lambda: {"ready": False, "commandpost": {"license_app": None}},
    )

    with pytest.raises(RuntimeError, match="doctor"):
        await service.dispatch(
            "start",
            prompt="remove gaps",
            session_id=None,
            required_operations=["remove_gaps"],
        )


@pytest.mark.anyio
async def test_timeline_service_applies_a_typed_edit_program():
    service = TimelineService(FakeTimelineController(), sessions=None, fcpxml=None)

    result = await service.dispatch(
        "apply",
        session_id="d" * 32,
        edit_program={
            "operations": [{"group": "edit", "action": "fill_gaps", "arguments": {}}],
            "changed_ranges": [[2.0, 3.0]],
        },
    )

    assert result["candidate"]["number"] == 1
    assert result["candidate"]["project_name"] == "Demo - AI Pass 1"


@pytest.mark.anyio
async def test_timeline_service_routes_undo_through_controller():
    class Sessions:
        def load(self, _session_id):
            return {"candidates": []}

    controller = FakeTimelineController()
    service = TimelineService(controller, Sessions(), fcpxml=None)

    result = await service.dispatch("undo", session_id="d" * 32, edit_program=None)

    assert result == {
        "session_id": "d" * 32,
        "project_name": "Demo - Undo 1",
        "fcpxml_path": "/tmp/undo-01.fcpxml",
    }
    assert controller.undo_calls == ["d" * 32]


def test_default_mcp_registry_has_concrete_services():
    services = build_default_services()

    assert type(services.session).__name__ == "SessionService"
    assert type(services.timeline).__name__ == "TimelineService"
    assert type(services.media).__name__ == "MediaService"
    assert type(services.verify).__name__ == "VerifyService"


def test_default_service_construction_does_not_require_watch_install(monkeypatch):
    def fail_if_eager(*_args, **_kwargs):
        raise FileNotFoundError("watch is not installed")

    monkeypatch.setattr("editor_cli.services.WatchAdapter.__init__", fail_if_eager)

    services = build_default_services()

    assert type(services.verify).__name__ == "VerifyService"


class RejectOnlyReviewController:
    async def record_review(self, *_args, **_kwargs):
        raise AssertionError("invalid review reached the controller")


@pytest.fixture
def ready_verify_service(tmp_path):
    sessions = SessionRepository(ControllerConfig(session_root=tmp_path / "sessions"))
    created = sessions.create(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )
    record = sessions.load(created["id"])
    paths = sessions.paths(record["id"])
    candidate_path = paths.candidates / "pass-01.fcpxml"
    candidate_path.write_text(
        """<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s"/>
        </resources><library><event name="Event"><project name="Candidate">
        <sequence format="r1" duration="12s"><spine/></sequence>
        </project></event></library></fcpxml>""",
        encoding="utf-8",
    )
    preview = paths.previews / "pass-01.mp4"
    preview.write_bytes(b"preview")
    evidence_root = paths.evidence / "pass-01"
    evidence_root.mkdir()
    frame = evidence_root / "frame-0001.jpg"
    frame.write_bytes(b"frame")
    manifest = evidence_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "preview": {
                    "path": str(preview),
                    "sha256": sha256(preview.read_bytes()).hexdigest(),
                },
                "frames": [{"path": str(frame), "timestamp_seconds": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    required_checks = (
        "candidate_xml_valid",
        "gap_removed",
        "preview_rendered",
        "preview_watched",
        "source_unchanged",
    )
    binding = EvidenceBinding(
        session_id=record["id"],
        pass_number=1,
        state_version=record["version"] + 1,
        project_name=f"Demo - {record['id'][:8]} - AI Pass 1",
        candidate_sha256=sha256(candidate_path.read_bytes()).hexdigest(),
        preview_sha256=sha256(preview.read_bytes()).hexdigest(),
        manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
        frame_timestamps=(1.0,),
    )
    record.update(
        {
            "state": "verify",
            "required_checks": list(required_checks),
            "candidates": [
                {
                    "number": 1,
                    "project_name": binding.project_name,
                    "fcpxml_path": str(candidate_path),
                    "preview_path": str(preview),
                    "evidence_manifest": str(manifest),
                    "duration_seconds": 12.0,
                    "media_references": [],
                    "binding": binding.to_dict(),
                    "controller_checks": {
                        "candidate_xml_valid": True,
                        "preview_rendered": True,
                        "preview_watched": True,
                        "source_unchanged": True,
                    },
                    "required_checks": {},
                    "observations": [],
                    "score": None,
                }
            ],
        }
    )
    sessions.save(record)
    service = VerifyService(RejectOnlyReviewController(), sessions, fcpxml=None)
    return SimpleNamespace(
        service=service,
        session_id=record["id"],
        binding=binding,
        required_checks=required_checks,
    )


@pytest.mark.anyio
async def test_review_cannot_replace_controller_required_checks(ready_verify_service):
    with pytest.raises(ServiceError, match="exact required checks"):
        await ready_verify_service.service.dispatch(
            "record",
            session_id=ready_verify_service.session_id,
            pass_number=1,
            report={
                "required": {"looks_good": True},
                "binding": ready_verify_service.binding.to_dict(),
            },
        )


@pytest.mark.anyio
async def test_review_rejects_stale_preview_hash(ready_verify_service):
    binding = ready_verify_service.binding.to_dict()
    binding["preview_sha256"] = "0" * 64
    with pytest.raises(ServiceError, match="preview hash"):
        await ready_verify_service.service.dispatch(
            "record",
            session_id=ready_verify_service.session_id,
            pass_number=1,
            report={
                "required": {
                    name: True for name in ready_verify_service.required_checks
                },
                "binding": binding,
            },
        )
