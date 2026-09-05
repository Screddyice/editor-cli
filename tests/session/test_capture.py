from types import SimpleNamespace

import pytest

from editor_cli.session.capture import (
    CaptureError,
    capture_active_project,
    file_sha256,
    recover_active_project_capture,
)
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
        return ProjectIdentity(
            identity.library, identity.event, name, identity.duration_seconds
        )


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
async def test_capture_persists_exact_file_media_references(tmp_path):
    media = (tmp_path / "outside" / "clip.mov").resolve()
    media.parent.mkdir()
    media.write_bytes(b"media")
    fake_fcp = FakeFinalCut([project("Demo")])

    async def export_with_media(identity, destination):
        fake_fcp.events.append("export")
        destination.write_text(
            '<fcpxml><resources><asset id="r1"><media-rep '
            f'kind="original-media" src="{media.as_uri()}"/>'
            f'</asset></resources><project name="{identity.project}"/></fcpxml>',
            encoding="utf-8",
        )

    fake_fcp.export_xml = export_with_media
    paths = SessionPaths.create(tmp_path / "sessions", "session1")
    result = await capture_active_project(fake_fcp, paths, SessionStore(paths.root))

    assert result.media_references == (media,)
    assert paths.require_read(media) == media


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
    identity = project("Demo")
    store.begin_external_action(
        "finalcut.export_xml",
        {"project": "Demo", "destination": str(paths.source / "active-source.fcpxml")},
        expected_identity=identity.__dict__,
        idempotency={"destination": str(paths.source / "active-source.fcpxml")},
    )
    fake_fcp = FakeFinalCut([project("Demo")])

    with pytest.raises(CaptureError, match="reconciliation"):
        await capture_active_project(fake_fcp, paths, store)

    assert fake_fcp.events == []


@pytest.mark.anyio
async def test_capture_recovery_uses_completed_export_without_replay(tmp_path):
    paths = SessionPaths.create(tmp_path, "session1")
    store = SessionStore(paths.root)
    identity = project("Demo")
    destination = paths.source / "active-source.fcpxml"
    destination.write_text('<fcpxml><project name="Demo"/></fcpxml>', encoding="utf-8")
    token = store.begin_external_action(
        "finalcut.export_xml",
        {"project": "Demo", "destination": str(destination)},
        expected_identity=identity.__dict__,
        idempotency={"destination": str(destination)},
    )
    store.complete_external_action(
        token,
        {
            "identity": identity.__dict__,
            "path": str(destination),
            "sha256": file_sha256(destination),
        },
    )
    fake_fcp = FakeFinalCut([identity])

    result = await recover_active_project_capture(fake_fcp, paths, store)

    assert result.identity == identity
    assert "export" not in fake_fcp.events
    assert fake_fcp.events == ["inspect", "identify", "duplicate"]
