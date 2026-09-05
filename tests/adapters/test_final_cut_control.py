import json
import subprocess
from pathlib import Path

import pytest

from editor_cli.adapters.final_cut_control import FinalCutControl, FinalCutControlError
from editor_cli.adapters.native_final_cut import (
    ExportReceipt,
    NativeFinalCutClient,
    NativeFinalCutError,
    NativeProbe,
    ShareReceipt,
)
from editor_cli.session.models import ProjectIdentity


@pytest.fixture
def anyio_backend():
    return "asyncio"


def project(name: str = "Demo") -> ProjectIdentity:
    return ProjectIdentity("Canary Library", "Canary Event", name, 12.0)


class FakeNative:
    def __init__(self):
        self.calls: list[tuple] = []
        self.active: ProjectIdentity | None = project()
        self.share_error: Exception | None = None

    def probe(self, session_root: Path) -> NativeProbe:
        self.calls.append(("probe", session_root))
        return NativeProbe(
            protocol_version=1,
            helper_sha256="a" * 64,
            final_cut_bundle_id="com.apple.FinalCutApp",
            final_cut_version="12.3",
            accessibility=True,
            automation=True,
            ready=True,
            dialogs=(),
            library_names=("Canary Library",),
            active_project=self.active,
        )

    def export_xml(
        self, identity: ProjectIdentity, destination: Path, session_root: Path
    ) -> ExportReceipt:
        self.calls.append(("export_xml", identity, destination, session_root))
        destination.write_text(
            '<fcpxml version="1.11"><project name="Demo"/></fcpxml>',
            encoding="utf-8",
        )
        return ExportReceipt("fcpxml_export", identity, destination.resolve())

    def duplicate_project(
        self, identity: ProjectIdentity, name: str, session_root: Path
    ) -> ProjectIdentity:
        self.calls.append(("duplicate_project", identity, name, session_root))
        duplicated = ProjectIdentity(
            identity.library,
            identity.event,
            name,
            identity.duration_seconds,
        )
        self.active = duplicated
        return duplicated

    def import_xml(
        self, identity: ProjectIdentity, source: Path, session_root: Path
    ) -> ProjectIdentity:
        self.calls.append(("import_xml", identity, source, session_root))
        self.active = identity
        return identity

    def share_preview(
        self, identity: ProjectIdentity, destination: Path, session_root: Path
    ) -> ShareReceipt:
        self.calls.append(("share_preview", identity, destination, session_root))
        if self.share_error is not None:
            raise self.share_error
        destination.write_bytes(b"final-cut-share")
        return ShareReceipt("final_cut_share", identity, destination.resolve())

    def open_project(
        self, identity: ProjectIdentity, session_root: Path
    ) -> ProjectIdentity:
        self.calls.append(("open_project", identity, session_root))
        self.active = identity
        return identity


class FakeFCPXML:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, arguments: dict):
        self.calls.append((tool, arguments))
        if tool == "preview":
            Path(arguments["args"]["output_path"]).write_bytes(b"diagnostic-proxy")
        return {"text": "ok"}


def control(tmp_path: Path, native: FakeNative | None = None):
    native = native or FakeNative()
    fcpxml = FakeFCPXML()
    return FinalCutControl(native, fcpxml, session_root=tmp_path), native, fcpxml


def test_final_cut_control_rejects_filesystem_root_session():
    with pytest.raises(FinalCutControlError, match="session root"):
        FinalCutControl(FakeNative(), FakeFCPXML(), session_root=Path("/"))


@pytest.mark.anyio
async def test_final_cut_control_reads_exact_active_project_from_native_probe(tmp_path):
    adapter, native, _fcpxml = control(tmp_path)

    projects = await adapter.active_projects()

    assert projects == (project(),)
    assert native.calls == [("probe", tmp_path.resolve())]


@pytest.mark.anyio
async def test_final_cut_control_reports_no_active_project_without_guessing(tmp_path):
    native = FakeNative()
    native.active = None
    adapter, _native, _fcpxml = control(tmp_path, native)

    assert await adapter.active_projects() == ()


@pytest.mark.anyio
async def test_final_cut_control_exports_only_through_native_helper(tmp_path):
    adapter, native, fcpxml = control(tmp_path)
    destination = tmp_path / "source" / "source.fcpxml"
    destination.parent.mkdir()

    await adapter.export_xml(project(), destination)

    assert destination.is_file()
    assert native.calls[-1] == (
        "export_xml",
        project(),
        destination.resolve(),
        tmp_path.resolve(),
    )
    assert fcpxml.calls == []


@pytest.mark.anyio
async def test_final_cut_control_imports_exact_candidate_through_native_helper(
    tmp_path,
):
    adapter, native, fcpxml = control(tmp_path)
    candidate = tmp_path / "candidates" / "pass-01.fcpxml"
    candidate.parent.mkdir()
    expected = project("Demo - AI Pass 1")
    candidate.write_text(
        '<fcpxml version="1.11"><project name="Demo - AI Pass 1"/></fcpxml>',
        encoding="utf-8",
    )

    imported = await adapter.import_project(candidate, expected)

    assert imported == expected
    assert native.calls[-1] == (
        "import_xml",
        expected,
        candidate.resolve(),
        tmp_path.resolve(),
    )
    assert fcpxml.calls == []


@pytest.mark.anyio
async def test_duplicate_enters_exact_name_and_returns_bound_identity(tmp_path):
    adapter, native, _fcpxml = control(tmp_path)

    duplicated = await adapter.duplicate_project(
        project(), "Demo - Before AI - abc12345"
    )

    assert duplicated == project("Demo - Before AI - abc12345")
    assert native.calls[-1] == (
        "duplicate_project",
        project(),
        "Demo - Before AI - abc12345",
        tmp_path.resolve(),
    )


@pytest.mark.anyio
async def test_render_preview_uses_final_cut_share_receipt(tmp_path):
    adapter, native, fcpxml = control(tmp_path)
    destination = tmp_path / "previews" / "pass-01.mov"
    destination.parent.mkdir()

    receipt = await adapter.render_preview(project(), destination)

    assert receipt == ShareReceipt("final_cut_share", project(), destination.resolve())
    assert destination.read_bytes() == b"final-cut-share"
    assert native.calls[-1] == (
        "share_preview",
        project(),
        destination.resolve(),
        tmp_path.resolve(),
    )
    assert fcpxml.calls == []


@pytest.mark.anyio
async def test_render_preview_never_falls_back_to_diagnostic_proxy(tmp_path):
    native = FakeNative()
    native.share_error = NativeFinalCutError("response identity changed")
    adapter, _native, fcpxml = control(tmp_path, native)
    destination = tmp_path / "previews" / "pass-01.mov"
    destination.parent.mkdir()

    with pytest.raises(FinalCutControlError, match="identity changed"):
        await adapter.render_preview(project(), destination)

    assert fcpxml.calls == []
    assert not destination.exists()


@pytest.mark.anyio
async def test_render_preview_translates_nul_receipt_path_error(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    bridge = tmp_path / "bridge"
    bridge.write_bytes(b"native helper")
    response = {
        "ok": True,
        "result": {
            "protocolVersion": 1,
            "kind": "final_cut_share",
            "project": {
                "library": "Canary Library",
                "event": "Canary Event",
                "project": "Demo",
                "duration_seconds": 12.0,
            },
            "output": str(root / "bad\x00.mov"),
        },
    }

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(response) + "\n",
            stderr="",
        )

    native = NativeFinalCutClient(bridge, runner=runner)
    adapter = FinalCutControl(native, FakeFCPXML(), session_root=root)

    with pytest.raises(FinalCutControlError, match="output"):
        await adapter.render_preview(project(), root / "pass.mov")


@pytest.mark.anyio
async def test_diagnostic_proxy_is_explicitly_ineligible_for_review(tmp_path):
    adapter, native, fcpxml = control(tmp_path)
    candidate = tmp_path / "candidates" / "pass-01.fcpxml"
    candidate.parent.mkdir()
    candidate.write_text("<fcpxml/>", encoding="utf-8")
    destination = tmp_path / "diagnostics" / "pass-01.mp4"
    destination.parent.mkdir()

    receipt = await adapter.render_diagnostic_proxy(candidate, destination)

    assert receipt.kind == "diagnostic_proxy"
    assert receipt.review_eligible is False
    assert receipt.output == destination.resolve()
    assert destination.read_bytes() == b"diagnostic-proxy"
    assert native.calls == []
    assert fcpxml.calls[-1][0] == "preview"


@pytest.mark.anyio
async def test_diagnostic_proxy_stays_inside_session(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    adapter, native, fcpxml = control(root)
    candidate = root / "candidates" / "pass-01.fcpxml"
    candidate.parent.mkdir()
    candidate.write_text("<fcpxml/>", encoding="utf-8")

    with pytest.raises(FinalCutControlError, match="outside the session"):
        await adapter.render_diagnostic_proxy(candidate, tmp_path / "outside.mp4")

    assert native.calls == []
    assert fcpxml.calls == []


@pytest.mark.anyio
async def test_open_project_requires_and_returns_exact_identity(tmp_path):
    adapter, native, _fcpxml = control(tmp_path)
    expected = project("Demo - AI Pass 1")

    opened = await adapter.open_project(expected)

    assert opened == expected
    assert native.calls[-1] == ("open_project", expected, tmp_path.resolve())
