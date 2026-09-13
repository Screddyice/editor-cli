import json
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from editor_cli.config import ControllerConfig
from editor_cli.mcp_server import build_default_services
from editor_cli.services import (
    MediaService,
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


@pytest.mark.anyio
async def test_timeline_service_rejects_unknown_program_and_operation_keys():
    service = TimelineService(FakeTimelineController(), sessions=None, fcpxml=None)
    with pytest.raises(ValueError, match="unexpected fields"):
        await service.dispatch(
            "apply",
            session_id="d" * 32,
            edit_program={"operations": [], "run_shell": True},
        )
    with pytest.raises(ValueError, match="unexpected fields"):
        await service.dispatch(
            "apply",
            session_id="d" * 32,
            edit_program={
                "operations": [
                    {
                        "group": "edit",
                        "action": "fill_gaps",
                        "arguments": {},
                        "path": "/tmp/private",
                    }
                ]
            },
        )


@pytest.mark.anyio
async def test_timeline_inspection_surfaces_installed_final_cut_assets(tmp_path):
    class Sessions:
        def load(self, _session_id):
            return {
                "state": "apply",
                "analysis": {
                    "clips": [],
                    "gaps": [],
                    "roles": [],
                    "markers": [],
                    "effects": [],
                    "transcript": [],
                    "pacing": {},
                },
                "capture": {"source_xml": "/tmp/source.fcpxml"},
                "candidates": [],
            }

    asset = SimpleNamespace(
        kind="title",
        name="Basic Title",
        category="Titles",
        action_id="Titles/Basic Title",
        handler="fcpx_title",
        path=tmp_path / "Basic Title.moti",
    )
    catalog = SimpleNamespace(scan=lambda: (asset,))
    service = TimelineService(
        FakeTimelineController(), Sessions(), fcpxml=None, asset_catalog=catalog
    )

    result = await service.dispatch("inspect", session_id="d" * 32, edit_program=None)

    assert result["analysis"]["clips"] == []
    assert result["installed_assets"] == [
        {
            "kind": "title",
            "name": "Basic Title",
            "category": "Titles",
            "action_id": "Titles/Basic Title",
            "handler": "fcpx_title",
        }
    ]


def test_default_mcp_registry_has_concrete_services():
    services = build_default_services()

    assert type(services.session).__name__ == "SessionService"
    assert type(services.timeline).__name__ == "TimelineService"
    assert type(services.media).__name__ == "MediaService"
    assert type(services.verify).__name__ == "VerifyService"
    assert type(services.timeline.asset_catalog).__name__ == "InstalledAssetCatalog"


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


class FakeRegistrationTimeline:
    def __init__(self):
        self.calls = []

    async def register_media(self, source, media, destination, **metadata):
        self.calls.append((source, media, destination, metadata))
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return SimpleNamespace(asset_id="r_editor_abc", path=destination)


@pytest.mark.anyio
async def test_media_service_registers_acquired_asset_for_timeline_use(tmp_path):
    sessions = SessionRepository(ControllerConfig(session_root=tmp_path / "sessions"))
    record = sessions.create(
        EditRequest("insert reaction", required_operations=("reaction_visible",))
    )
    paths = sessions.paths(record["id"])
    source = paths.source / "active-source.fcpxml"
    source.write_text("<fcpxml><resources/></fcpxml>", encoding="utf-8")
    record["capture"] = {"source_xml": str(source), "source_sha256": "0" * 64}
    record["state"] = "apply"
    sessions.save(record)
    media = paths.assets / "reaction.mp4"
    media.write_bytes(b"movie")
    (paths.assets / "provenance.jsonl").write_text(
        json.dumps(
            {
                "path": str(media),
                "sha256": sha256(media.read_bytes()).hexdigest(),
                "source_url": "https://example.com/reaction.mp4",
                "purpose": "reaction",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    timeline = FakeRegistrationTimeline()
    service = MediaService(
        sessions, timeline=timeline, probe_media=lambda _path: (True, False)
    )

    result = await service.dispatch(
        "register",
        session_id=record["id"],
        url=None,
        purpose=None,
        asset_path=str(media),
        name="Reaction",
        duration_seconds=1.0,
        has_audio=False,
    )

    assert result["asset_id"] == "r_editor_abc"
    saved = sessions.load(record["id"])
    assert saved["registered_assets"][0]["path"] == str(media.resolve())
    assert saved["working_xml"].endswith("register-01.fcpxml")
    assert timeline.calls[0][3]["has_video"] is True
    assert timeline.calls[0][3]["has_audio"] is False


@pytest.mark.anyio
async def test_media_service_rejects_acquired_asset_changed_after_download(tmp_path):
    sessions = SessionRepository(ControllerConfig(session_root=tmp_path / "sessions"))
    record = sessions.create(
        EditRequest("insert reaction", required_operations=("reaction_visible",))
    )
    paths = sessions.paths(record["id"])
    source = paths.source / "source.fcpxml"
    source.write_text("<fcpxml><resources/></fcpxml>", encoding="utf-8")
    record.update({"capture": {"source_xml": str(source)}, "state": "apply"})
    sessions.save(record)
    media = paths.assets / "reaction.mp4"
    media.write_bytes(b"original")
    (paths.assets / "provenance.jsonl").write_text(
        json.dumps(
            {"path": str(media), "sha256": sha256(media.read_bytes()).hexdigest()}
        )
        + "\n",
        encoding="utf-8",
    )
    media.write_bytes(b"changed")
    service = MediaService(
        sessions,
        timeline=FakeRegistrationTimeline(),
        probe_media=lambda _path: (True, False),
    )

    with pytest.raises(PermissionError, match="acquired session asset"):
        await service.dispatch(
            "register",
            session_id=record["id"],
            url=None,
            purpose=None,
            asset_path=str(media),
            name="Reaction",
            duration_seconds=1.0,
        )


@pytest.mark.anyio
async def test_media_service_uses_probe_for_audio_only_mp4(tmp_path):
    sessions = SessionRepository(ControllerConfig(session_root=tmp_path / "sessions"))
    record = sessions.create(
        EditRequest("add music", required_operations=("add_audio",))
    )
    paths = sessions.paths(record["id"])
    source = paths.source / "source.fcpxml"
    source.write_text("<fcpxml><resources/></fcpxml>", encoding="utf-8")
    record.update({"capture": {"source_xml": str(source)}, "state": "apply"})
    sessions.save(record)
    media = paths.assets / "audio-only.mp4"
    media.write_bytes(b"audio container")
    (paths.assets / "provenance.jsonl").write_text(
        json.dumps(
            {"path": str(media), "sha256": sha256(media.read_bytes()).hexdigest()}
        )
        + "\n",
        encoding="utf-8",
    )
    timeline = FakeRegistrationTimeline()
    service = MediaService(
        sessions, timeline=timeline, probe_media=lambda _path: (False, True)
    )

    await service.dispatch(
        "register",
        session_id=record["id"],
        url=None,
        purpose=None,
        asset_path=str(media),
        name="Music",
        duration_seconds=1.0,
    )

    assert timeline.calls[0][3]["has_video"] is False
    assert timeline.calls[0][3]["has_audio"] is True


def test_media_probe_reads_real_audio_only_mp4_streams(tmp_path):
    media = tmp_path / "audio-only.mp4"
    subprocess.run(
        (
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.1",
            "-c:a",
            "aac",
            str(media),
        ),
        check=True,
    )

    assert MediaService._probe_media(media) == (False, True)


def test_media_probe_rejects_disguised_playlist(tmp_path):
    import wave

    neighbor = tmp_path / "unselected.wav"
    with wave.open(str(neighbor), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)
    playlist = tmp_path / "download.mp4"
    playlist.write_text(f"ffconcat version 1.0\nfile '{neighbor.name}'\n")
    with pytest.raises(ValueError, match="not readable media"):
        MediaService._probe_media(playlist)


@pytest.mark.anyio
async def test_media_service_refuses_unacquired_neighbor_file(tmp_path):
    sessions = SessionRepository(ControllerConfig(session_root=tmp_path / "sessions"))
    record = sessions.create(
        EditRequest("insert reaction", required_operations=("reaction_visible",))
    )
    neighbor = tmp_path / "private.mp4"
    neighbor.write_bytes(b"private")
    service = MediaService(sessions, timeline=FakeRegistrationTimeline())

    with pytest.raises(PermissionError):
        await service.dispatch(
            "register",
            session_id=record["id"],
            url=None,
            purpose=None,
            asset_path=str(neighbor),
            name="Private",
            duration_seconds=1.0,
            has_audio=False,
        )
