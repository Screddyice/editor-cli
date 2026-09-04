import json
from types import SimpleNamespace

import pytest

from editor_cli.config import ControllerConfig
from editor_cli.session.controller import (
    ControllerDeps,
    EditSessionController,
    SessionError,
    SessionRepository,
)
from editor_cli.session.models import (
    EditOperation,
    EditProgram,
    EditRequest,
    ProjectIdentity,
    SessionState,
)
from editor_cli.verification.review import ReviewReport


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeFinalCut:
    def __init__(self):
        self.identity = ProjectIdentity("Library", "Event", "Demo", 12.0)
        self.source_bytes = b'<fcpxml><project name="Demo"/></fcpxml>'
        self.opened_project = None
        self.imported = []

    async def active_projects(self):
        return (self.identity,)

    async def export_xml(self, _identity, destination):
        destination.write_bytes(self.source_bytes)

    async def inspect_xml(self, _path):
        return SimpleNamespace(
            project=self.identity.project,
            duration_seconds=self.identity.duration_seconds,
            frame_seconds=1 / 30,
        )

    async def duplicate_project(self, _identity, _name):
        return None

    async def import_project(self, path, project_name):
        self.imported.append((path, project_name))

    async def render_preview(self, _project_name, destination):
        destination.write_bytes(b"rendered preview")

    async def open_project(self, project_name):
        self.opened_project = project_name


class FakeTimeline:
    async def analyze(self, _source):
        return {"duration_seconds": 12.0, "clips": 2, "gaps": 1}

    async def apply(self, source, program, destination):
        destination.write_text(
            source.read_text(encoding="utf-8")
            + f"\n<!-- {program.operations[0].action} -->\n",
            encoding="utf-8",
        )
        return destination


class FakeWatch:
    def analyze(self, preview, out, changed_ranges):
        out.mkdir(parents=True, exist_ok=True)
        manifest = out / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "preview": str(preview),
                    "changed_ranges": changed_ranges,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(manifest=manifest)


def controller_deps(tmp_path):
    config = ControllerConfig(session_root=tmp_path / "sessions")
    fcp = FakeFinalCut()
    return ControllerDeps(
        sessions=SessionRepository(config),
        fcp=fcp,
        timeline=FakeTimeline(),
        watch=FakeWatch(),
    )


def valid_edit_program():
    return EditProgram(
        operations=(EditOperation("edit", "fill_gaps", {}),),
        changed_ranges=((2.0, 3.0),),
    )


def verified_report():
    return ReviewReport(
        required={"preview_rendered": True, "gap_removed": True},
        observations=(),
        changed_ranges=((2.0, 3.0),),
    )


def failed_report(score):
    passed = round(score * 10)
    return ReviewReport(
        required={f"check_{index}": index < passed for index in range(10)},
        observations=("One or more required checks failed",),
    )


@pytest.mark.anyio
async def test_controller_finishes_after_verified_first_pass(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(EditRequest("remove gaps"))
    candidate = await controller.apply(session.id, valid_edit_program())
    result = await controller.record_review(
        session.id, candidate.number, verified_report()
    )
    assert result.state is SessionState.READY
    assert result.passes == 1
    assert result.best_pass.number == 1
    assert deps.fcp.opened_project == candidate.project_name
    assert (session.root / "source" / "active-source.fcpxml").read_bytes() == deps.fcp.source_bytes


@pytest.mark.anyio
async def test_controller_caps_at_three_and_leaves_best_candidate(tmp_path):
    deps = controller_deps(tmp_path)
    controller = EditSessionController(deps)
    session = await controller.start(EditRequest("make it funny"))
    for score in (0.4, 0.7, 0.6):
        candidate = await controller.apply(session.id, valid_edit_program())
        result = await controller.record_review(
            session.id, candidate.number, failed_report(score)
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
    session = await first.start(EditRequest("remove gaps"))

    resumed = EditSessionController(deps)
    status = resumed.status(session.id)
    assert status.state is SessionState.APPLY
    candidate = await resumed.apply(session.id, valid_edit_program())
    assert candidate.number == 1
