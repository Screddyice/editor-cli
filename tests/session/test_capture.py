from types import SimpleNamespace

import pytest

from editor_cli.session.capture import CaptureError, capture_active_project, file_sha256
from editor_cli.session.models import ProjectIdentity
from editor_cli.session.paths import SessionPaths
from editor_cli.session.store import SessionStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


def project(name: str, duration: float = 12.0) -> ProjectIdentity:
    return ProjectIdentity(
        library="Canary Library",
        event="Canary Event",
        project=name,
        duration_seconds=duration,
    )


class FakeFinalCut:
    def __init__(self, projects):
        self.projects = projects
        self.events = []

    async def active_projects(self):
        self.events.append("identify")
        return tuple(self.projects)

    async def export_xml(self, identity, destination):
        self.events.append("export")
        destination.write_text(
            f'<fcpxml><project name="{identity.project}"/></fcpxml>',
            encoding="utf-8",
        )

    async def inspect_xml(self, path):
        self.events.append("inspect")
        identity = self.projects[0]
        return SimpleNamespace(
            project=identity.project,
            duration_seconds=identity.duration_seconds,
            frame_seconds=1 / 30,
        )

    async def duplicate_project(self, identity, name):
        self.events.append("duplicate")


@pytest.mark.anyio
async def test_capture_rejects_ambiguous_active_project(tmp_path):
    fake_fcp = FakeFinalCut([project("A"), project("B")])
    with pytest.raises(CaptureError, match="select one project"):
        await capture_active_project(
            fake_fcp, SessionPaths.create(tmp_path, "session1")
        )


@pytest.mark.anyio
async def test_capture_preserves_source_before_candidate(tmp_path):
    fake_fcp = FakeFinalCut([project("Demo")])
    paths = SessionPaths.create(tmp_path, "session1")
    result = await capture_active_project(fake_fcp, paths, SessionStore(paths.root))
    assert result.source_xml.name == "active-source.fcpxml"
    assert result.preserved_name.startswith("Demo - Before AI - ")
    assert file_sha256(result.source_xml) == result.source_sha256
    assert fake_fcp.events == ["identify", "export", "inspect", "duplicate"]


@pytest.mark.anyio
async def test_capture_rejects_exported_identity_mismatch(tmp_path):
    fake_fcp = FakeFinalCut([project("Demo")])

    async def wrong_inspection(_path):
        fake_fcp.events.append("inspect")
        return SimpleNamespace(
            project="Wrong Project", duration_seconds=12.0, frame_seconds=1 / 30
        )

    fake_fcp.inspect_xml = wrong_inspection
    with pytest.raises(CaptureError, match="identity"):
        await capture_active_project(
            fake_fcp, SessionPaths.create(tmp_path, "session1")
        )
    assert "duplicate" not in fake_fcp.events


@pytest.mark.anyio
async def test_capture_does_not_replay_pending_external_action(tmp_path):
    paths = SessionPaths.create(tmp_path, "session1")
    store = SessionStore(paths.root)
    store.begin_external_action("commandpost.export", {"project": "Demo"})
    fake_fcp = FakeFinalCut([project("Demo")])

    with pytest.raises(CaptureError, match="reconciliation"):
        await capture_active_project(fake_fcp, paths, store)

    assert fake_fcp.events == ["identify"]
