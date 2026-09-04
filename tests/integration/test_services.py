from pathlib import Path
from types import SimpleNamespace

import pytest

from editor_cli.mcp_server import build_default_services
from editor_cli.services import SessionService, TimelineService
from editor_cli.session.controller import Candidate, SessionResult
from editor_cli.session.models import SessionState


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeController:
    def __init__(self):
        self.calls = []

    async def start(self, request):
        self.calls.append(("start", request.prompt))
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


@pytest.mark.anyio
async def test_session_service_serializes_start_status_and_resume():
    controller = FakeController()
    service = SessionService(controller, doctor=lambda: {"ready": True})

    started = await service.dispatch("start", prompt="remove gaps", session_id=None)
    status = await service.dispatch("status", prompt=None, session_id="b" * 32)
    resumed = await service.dispatch("resume", prompt=None, session_id="c" * 32)

    assert started["session_id"] == "a" * 32
    assert started["state"] == "apply"
    assert status["best_candidate"]["number"] == 2
    assert status["failed_checks"] == ["meme_insert"]
    assert resumed["state"] == "correct"


@pytest.mark.anyio
async def test_session_service_refuses_start_when_device_is_not_ready():
    service = SessionService(
        FakeController(),
        doctor=lambda: {"ready": False, "commandpost": {"license_app": None}},
    )

    with pytest.raises(RuntimeError, match="doctor"):
        await service.dispatch("start", prompt="remove gaps", session_id=None)


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


def test_default_mcp_registry_has_concrete_services():
    services = build_default_services()

    assert type(services.session).__name__ == "SessionService"
    assert type(services.timeline).__name__ == "TimelineService"
    assert type(services.media).__name__ == "MediaService"
    assert type(services.verify).__name__ == "VerifyService"
