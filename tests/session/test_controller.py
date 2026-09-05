import json
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from editor_cli.config import ControllerConfig
from editor_cli.session.controller import (
    ControllerDeps,
    EditSessionController,
    SessionError,
    SessionRepository,
)
from editor_cli.session.locking import SessionBusy, SessionLock
from editor_cli.session.models import (
    BASE_REQUIRED_CHECKS,
    EditOperation,
    EditProgram,
    EditRequest,
    ProjectIdentity,
    SessionState,
)
from editor_cli.verification.review import ReviewReport
from editor_cli.verification.technical import inspect_candidate_fcpxml


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeFinalCut:
    def __init__(self, media_path):
        self.identity = ProjectIdentity("Library", "Event", "Demo", 12.0)
        self.source_bytes = f"""<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
        <asset id="r2"><media-rep src="{media_path.as_uri()}"/></asset>
        </resources><library><event name="Event"><project name="Demo">
        <sequence format="r1" duration="12s"><spine>
        <asset-clip ref="r2" offset="0s" duration="12s"/>
        </spine></sequence>
        </project></event></library></fcpxml>""".encode()
        self.opened_project = None
        self.imported = []
        self.rendered = []
        self.duplicated = []
        self.reconciled = []

    async def active_projects(self):
        return (self.identity,)

    async def export_xml(self, _identity, destination):
        destination.write_bytes(self.source_bytes)

    async def inspect_xml(self, _path):
        return SimpleNamespace(
            project="Demo",
            duration_seconds=12.0,
            frame_seconds=1 / 30,
        )

    async def duplicate_project(self, _identity, _name):
        self.duplicated.append(_name)
        self.identity = ProjectIdentity("Library", "Event", _name, 12.0)
        return self.identity

    async def import_project(self, path, expected_identity):
        self.imported.append((path, expected_identity.project))
        self.identity = expected_identity
        return expected_identity

    async def render_preview(self, identity, destination):
        self.rendered.append(identity)
        destination.write_bytes(b"rendered preview")

    async def open_project(self, identity):
        self.opened_project = identity.project
        self.identity = identity
        return identity

    async def reconcile_external_action(self, action):
        self.reconciled.append(action.action)
        return {"reconciled": True, "project": self.identity.project}


class FakeTimeline:
    def __init__(self):
        self.candidate_xml = None

    async def analyze(self, _source):
        return {"duration_seconds": 12.0, "clips": 2, "gaps": 1}

    async def apply(self, source, program, destination):
        value = self.candidate_xml or (
            source.read_text(encoding="utf-8")
            + f"\n<!-- {program.operations[0].action} -->\n"
        )
        destination.write_text(value, encoding="utf-8")
        return destination


class FakeWatch:
    def analyze(self, preview, out, changed_ranges):
        out.mkdir(parents=True, exist_ok=True)
        frame = out / "frame-0001.jpg"
        frame.write_bytes(b"frame")
        manifest = out / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "preview": {
                        "path": str(preview),
                        "sha256": sha256(preview.read_bytes()).hexdigest(),
                    },
                    "frames": [
                        {
                            "path": str(frame),
                            "timestamp_seconds": 2.5,
                            "reason": "changed range",
                            "scope": "full",
                        }
                    ],
                    "changed_ranges": changed_ranges,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(manifest=manifest)


def controller_deps(tmp_path):
    config = ControllerConfig(session_root=tmp_path / "sessions")
    media = tmp_path / "source.mov"
    media.write_bytes(b"source media")
    fcp = FakeFinalCut(media)

    async def validate_candidate(path):
        return inspect_candidate_fcpxml(
            path, upstream_validation={"text": "## Health Score: 100%"}
        )

    return ControllerDeps(
        sessions=SessionRepository(config),
        fcp=fcp,
        timeline=FakeTimeline(),
        watch=FakeWatch(),
        candidate_validator=validate_candidate,
        preview_inspector=lambda *_args, **_kwargs: ReviewReport(
            required={"preview_usable": True}, observations=()
        ),
    )


def test_controller_dependencies_require_candidate_validator(tmp_path):
    media = tmp_path / "source.mov"
    media.write_bytes(b"source media")

    with pytest.raises(TypeError, match="candidate_validator"):
        ControllerDeps(
            sessions=SessionRepository(
                ControllerConfig(session_root=tmp_path / "sessions")
            ),
            fcp=FakeFinalCut(media),
            timeline=FakeTimeline(),
            watch=FakeWatch(),
        )


def valid_edit_program():
    return EditProgram(
        operations=(EditOperation("edit", "fill_gaps", {}),),
        changed_ranges=((2.0, 3.0),),
    )


def verified_report(candidate):
    return ReviewReport(
        required={name: True for name in candidate.required_check_names},
        observations=(),
        changed_ranges=((2.0, 3.0),),
        binding=candidate.binding,
    )


def failed_report(candidate, score):
    operation_checks = tuple(
        name
        for name in candidate.required_check_names
        if name not in BASE_REQUIRED_CHECKS
    )
    passed = round(score * len(operation_checks))
    return ReviewReport(
        required={
            name: (
                True
                if name in BASE_REQUIRED_CHECKS
                else operation_checks.index(name) < passed
            )
            for name in candidate.required_check_names
        },
        observations=("One or more required checks failed",),
        binding=candidate.binding,
    )


@pytest.mark.anyio
async def test_controller_finishes_after_verified_first_pass(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    candidate = await controller.apply(session.id, valid_edit_program())
    result = await controller.record_review(
        session.id, candidate.number, verified_report(candidate)
    )
    assert result.state is SessionState.READY
    assert result.passes == 1
    assert result.best_pass.number == 1
    assert deps.fcp.opened_project == candidate.project_name
    assert (
        session.root / "source" / "active-source.fcpxml"
    ).read_bytes() == deps.fcp.source_bytes


@pytest.mark.anyio
async def test_controller_persists_exact_required_checks_at_session_start(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)

    session = await controller.start(
        EditRequest(
            "remove gaps and add a reaction title",
            required_operations=("remove_gaps", "add_title", "insert_reaction"),
        )
    )

    record = deps.sessions.load(session.id)
    assert record["required_checks"] == [
        "candidate_xml_valid",
        "gap_removed",
        "preview_rendered",
        "preview_watched",
        "reaction_insert_visible",
        "source_unchanged",
        "title_visible",
    ]


def test_repository_rejects_unknown_operation_without_creating_session(tmp_path):
    sessions_root = tmp_path / "sessions"
    repository = SessionRepository(ControllerConfig(session_root=sessions_root))

    with pytest.raises(ValueError, match="Unsupported required edit operation"):
        repository.create(
            EditRequest("do something unsupported", required_operations=("unknown",))
        )

    assert not sessions_root.exists()


@pytest.mark.anyio
async def test_candidate_binding_persists_current_version_and_artifact_hashes(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )

    candidate = await controller.apply(session.id, valid_edit_program())
    record = deps.sessions.load(session.id)

    assert candidate.binding.state_version == record["version"]
    assert (
        candidate.binding.candidate_sha256
        == sha256(candidate.fcpxml_path.read_bytes()).hexdigest()
    )
    assert (
        candidate.binding.preview_sha256
        == sha256(candidate.preview_path.read_bytes()).hexdigest()
    )
    assert (
        candidate.binding.manifest_sha256
        == sha256(candidate.evidence_manifest.read_bytes()).hexdigest()
    )
    assert candidate.binding.frame_timestamps == (2.5,)


@pytest.mark.anyio
async def test_controller_rejects_candidate_mutated_during_quality_control(tmp_path):
    deps = controller_deps(tmp_path)
    original_validator = deps.candidate_validator

    async def mutate_after_inspection(path):
        result = await original_validator(path)
        path.write_text(path.read_text(encoding="utf-8") + "\n<!-- changed -->")
        return result

    controller = EditSessionController(
        replace(deps, candidate_validator=mutate_after_inspection)
    )
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )

    with pytest.raises(SessionError, match="changed during inspection"):
        await controller.apply(session.id, valid_edit_program())

    assert deps.fcp.imported == []


@pytest.mark.anyio
async def test_controller_rejects_preview_mutated_during_quality_control(tmp_path):
    deps = controller_deps(tmp_path)

    def mutate_after_inspection(path, **_kwargs):
        path.write_bytes(b"mutated preview")
        return ReviewReport(required={"preview_usable": True}, observations=())

    controller = EditSessionController(
        replace(deps, preview_inspector=mutate_after_inspection)
    )
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )

    with pytest.raises(SessionError, match="Preview changed during inspection"):
        await controller.apply(session.id, valid_edit_program())


@pytest.mark.anyio
async def test_controller_rejects_manifest_mutated_during_quality_control(
    tmp_path, monkeypatch
):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )
    original_read_text = type(tmp_path).read_text

    def mutate_after_read(path, *args, **kwargs):
        value = original_read_text(path, *args, **kwargs)
        if path.name == "manifest.json":
            path.write_text(value + "\n", encoding="utf-8")
        return value

    monkeypatch.setattr(type(tmp_path), "read_text", mutate_after_read)

    with pytest.raises(SessionError, match="manifest changed during inspection"):
        await controller.apply(session.id, valid_edit_program())


@pytest.mark.anyio
async def test_controller_rechecks_preserved_source_before_accepting_review(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )
    candidate = await controller.apply(session.id, valid_edit_program())
    source = session.root / "source" / "active-source.fcpxml"
    source.write_text(source.read_text(encoding="utf-8") + "\n<!-- changed -->")

    with pytest.raises(SessionError, match="preserved source XML changed"):
        await controller.record_review(
            session.id, candidate.number, verified_report(candidate)
        )

    assert deps.fcp.opened_project is None


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("fcpxml_path", "Candidate XML hash changed after inspection"),
        ("preview_path", "Review preview hash changed after inspection"),
        ("evidence_manifest", "Review manifest hash changed after inspection"),
    ],
)
@pytest.mark.anyio
async def test_controller_reports_missing_bound_artifact_as_session_error(
    tmp_path, artifact, message
):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )
    candidate = await controller.apply(session.id, valid_edit_program())
    getattr(candidate, artifact).unlink()

    with pytest.raises(SessionError, match=message):
        await controller.record_review(
            session.id, candidate.number, verified_report(candidate)
        )


@pytest.mark.anyio
async def test_controller_uses_candidate_duration_for_native_identity(tmp_path):
    deps = controller_deps(tmp_path)
    deps.timeline.candidate_xml = deps.fcp.source_bytes.decode().replace(
        'duration="12s"', 'duration="7s"'
    )
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("remove_gaps",))
    )

    candidate = await controller.apply(session.id, valid_edit_program())

    assert candidate.duration_seconds == 7.0
    assert deps.fcp.imported[-1][1] == candidate.project_name
    assert deps.fcp.rendered[-1].duration_seconds == 7.0


@pytest.mark.anyio
async def test_controller_rejects_candidate_with_missing_media_before_import(tmp_path):
    deps = controller_deps(tmp_path)
    missing = tmp_path / "missing.mov"
    deps.timeline.candidate_xml = (
        deps.fcp.source_bytes.decode()
        .replace(
            "<resources>",
            f'<resources><asset id="r3"><media-rep src="{missing.as_uri()}"/></asset>',
        )
        .replace(
            "</spine>",
            '<asset-clip ref="r3" offset="0s" duration="1s"/></spine>',
        )
    )
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("insert reaction", required_operations=("insert_reaction",))
    )

    with pytest.raises(SessionError, match="missing media"):
        await controller.apply(session.id, valid_edit_program())

    assert deps.fcp.imported == []


@pytest.mark.anyio
async def test_controller_caps_at_three_and_leaves_best_candidate(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest(
            "make it funny",
            required_operations=(
                "meme_visible",
                "add_title",
                "insert_reaction",
                "change_speed",
                "add_transition",
            ),
        )
    )
    for score in (0.4, 0.7, 0.6):
        candidate = await controller.apply(session.id, valid_edit_program())
        result = await controller.record_review(
            session.id, candidate.number, failed_report(candidate, score)
        )
    assert result.state is SessionState.BLOCKED
    assert result.passes == 3
    assert result.best_pass.number == 2
    assert deps.fcp.opened_project == result.best_pass.project_name
    with pytest.raises(SessionError, match="three passes"):
        await controller.apply(session.id, valid_edit_program())


@pytest.mark.anyio
async def test_controller_resumes_from_persisted_state(tmp_path):
    deps = controller_deps(tmp_path)
    first = EditSessionController(deps)
    session = await first.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )

    resumed = EditSessionController(deps)
    status = resumed.status(session.id)
    assert status.state is SessionState.APPLY
    candidate = await resumed.apply(session.id, valid_edit_program())
    assert candidate.number == 1


@pytest.mark.anyio
async def test_controller_resume_revalidates_the_active_project(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )

    resumed = await EditSessionController(deps).resume(session.id)

    assert resumed.id == session.id
    assert resumed.identity == session.identity
    assert resumed.state is SessionState.APPLY


@pytest.mark.anyio
async def test_controller_resume_rejects_a_different_active_project(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    deps.fcp.identity = ProjectIdentity("Library", "Event", "Other", 12.0)

    with pytest.raises(SessionError, match="active project changed"):
        await EditSessionController(deps).resume(session.id)


@pytest.mark.anyio
async def test_controller_rejects_review_keys_or_binding_not_owned_by_candidate(
    tmp_path,
):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    candidate = await controller.apply(session.id, valid_edit_program())

    with pytest.raises(SessionError, match="required checks"):
        await controller.record_review(
            session.id,
            candidate.number,
            ReviewReport(
                required={"caller_invented": True},
                observations=(),
                binding=candidate.binding,
            ),
        )
    stale = candidate.binding.to_dict()
    stale["preview_sha256"] = "f" * 64
    with pytest.raises(SessionError, match="preview hash"):
        await controller.record_review(
            session.id,
            candidate.number,
            ReviewReport(
                required={name: True for name in candidate.required_check_names},
                observations=(),
                binding=type(candidate.binding).from_dict(stale),
            ),
        )


@pytest.mark.anyio
async def test_controller_names_candidates_with_short_session_id(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )

    candidate = await controller.apply(session.id, valid_edit_program())

    assert session.id[:8] in candidate.project_name


@pytest.mark.anyio
async def test_resume_reconciles_actionable_pending_open(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    store = deps.sessions.store(session.id)
    expected = deps.fcp.identity
    store.begin_external_action(
        "finalcut.open_project",
        {"project_name": expected.project},
        expected_identity=expected.__dict__,
        idempotency={"project_name": expected.project},
    )

    resumed = await EditSessionController(deps).resume(session.id)

    assert resumed.id == session.id
    assert deps.fcp.opened_project is None
    assert store.pending_actions() == []


@pytest.mark.anyio
async def test_resume_fails_closed_for_multiple_pending_actions(tmp_path):
    deps = controller_deps(tmp_path)
    session = await EditSessionController(deps).start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    store = deps.sessions.store(session.id)
    expected = deps.fcp.identity
    for _ in range(2):
        store.begin_external_action(
            "finalcut.open_project",
            {"identity": expected.__dict__},
            expected_identity=expected.__dict__,
            idempotency={"project_name": expected.project},
        )

    with pytest.raises(SessionError, match="multiple pending"):
        await EditSessionController(deps).resume(session.id)

    assert len(store.pending_actions()) == 2


@pytest.mark.anyio
async def test_apply_fails_under_competing_session_lock_before_timeline_mutation(
    tmp_path,
):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )

    with SessionLock(session.root), pytest.raises(SessionBusy):
        await controller.apply(session.id, valid_edit_program())

    assert list((session.root / "candidates").iterdir()) == []


@pytest.mark.anyio
async def test_resume_reconciles_completed_import_without_replay(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    record = deps.sessions.load(session.id)
    candidate = deps.sessions.paths(session.id).candidates / "pass-01.fcpxml"
    candidate.write_bytes(deps.fcp.source_bytes)
    expected = ProjectIdentity(
        "Library", "Event", f"Demo - {session.id[:8]} - AI Pass 1", 12.0
    )
    deps.fcp.identity = expected
    deps.fcp.imported.clear()
    record["state"] = SessionState.IMPORT.value
    deps.sessions.save(record)
    store = deps.sessions.store(session.id)
    store.begin_external_action(
        "finalcut.import_xml",
        {"path": str(candidate), "identity": expected.__dict__},
        expected_identity=expected.__dict__,
        idempotency={
            "candidate_sha256": sha256(candidate.read_bytes()).hexdigest(),
            "project_name": expected.project,
        },
    )

    result = await controller.resume(session.id)

    assert result.state is SessionState.PREVIEW
    assert deps.fcp.imported == []
    assert store.pending_actions() == []


@pytest.mark.anyio
async def test_resume_reconstructs_capture_after_completed_duplicate_without_replay(
    tmp_path,
):
    deps = controller_deps(tmp_path)
    record = deps.sessions.create(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    record["state"] = SessionState.CAPTURE.value
    deps.sessions.save(record)
    paths = deps.sessions.paths(record["id"])
    store = deps.sessions.store(record["id"])
    original = deps.fcp.identity
    source = paths.source / "active-source.fcpxml"
    source.write_bytes(deps.fcp.source_bytes)
    export_token = store.begin_external_action(
        "finalcut.export_xml",
        {"project": original.project, "destination": str(source)},
        expected_identity=original.__dict__,
        idempotency={"destination": str(source)},
    )
    store.complete_external_action(
        export_token,
        {
            "identity": original.__dict__,
            "path": str(source),
            "sha256": sha256(source.read_bytes()).hexdigest(),
        },
    )
    preserved = ProjectIdentity(
        original.library,
        original.event,
        f"Demo - Before AI - {record['id'][:8]}",
        original.duration_seconds,
    )
    store.begin_external_action(
        "finalcut.duplicate_project",
        {"project": original.project, "preserved_name": preserved.project},
        expected_identity=preserved.__dict__,
        idempotency={"project_name": preserved.project},
    )
    deps.fcp.identity = preserved
    deps.fcp.duplicated.clear()

    result = await EditSessionController(deps).resume(record["id"])

    assert result.state is SessionState.APPLY
    assert result.identity == original
    assert deps.fcp.duplicated == []
    assert store.pending_actions() == []


@pytest.mark.anyio
async def test_undo_is_journaled_and_creates_a_new_project_version(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(
        EditRequest("remove gaps", required_operations=("gap_removed",))
    )
    first = await controller.apply(session.id, valid_edit_program())
    await controller.record_review(session.id, first.number, failed_report(first, 0.5))
    await controller.apply(session.id, valid_edit_program())

    result = await controller.undo(session.id)

    assert result.project_name.endswith("Undo 1")
    assert deps.fcp.imported[-1][0] == result.fcpxml_path
    assert deps.fcp.opened_project == result.project_name
    kinds = [event["kind"] for event in deps.sessions.store(session.id).events()]
    assert "undo_created" in kinds
