import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fcpxml.writer import FCPXMLModifier

from editor_cli.config import ControllerConfig
from editor_cli.session.controller import (
    ControllerDeps,
    EditSessionController,
    SessionRepository,
)
from editor_cli.session.models import ProjectIdentity
from editor_cli.verification.review import ReviewReport

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "fcp_live_canary.py"
_FIXTURE = _ROOT / "tests" / "fixtures" / "canary" / "expected.json"
_SPEC = importlib.util.spec_from_file_location("fcp_live_canary", _SCRIPT)
assert _SPEC and _SPEC.loader
fcp_live_canary = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = fcp_live_canary
_SPEC.loader.exec_module(fcp_live_canary)


def _expected() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_canary_definition_covers_visible_and_structural_edits():
    expected = _expected()
    assert set(expected["required_checks"]) == set(fcp_live_canary.EXPECTED_CHECKS)
    assert expected["duration_seconds"] == 8.0
    assert set(expected["passing_result"]) == set(fcp_live_canary.EXPECTED_CHECKS)
    assert all(type(value) is bool for value in expected["passing_result"].values())


def test_create_canary_workspace_creates_only_disposable_paths(tmp_path):
    root = tmp_path / "canary"

    workspace = fcp_live_canary.create_canary_workspace(root)

    assert workspace.root == root.resolve()
    assert workspace.source.is_dir()
    assert workspace.sessions.is_dir()
    assert not workspace.library.exists()
    assert workspace.library.parent == workspace.root
    with pytest.raises(FileExistsError):
        fcp_live_canary.create_canary_workspace(root)


def test_create_source_builds_an_eight_second_timeline(monkeypatch, tmp_path):
    workspace = fcp_live_canary.create_canary_workspace(tmp_path / "canary")

    def render(destination, **_kwargs):
        destination.write_bytes(b"generated canary media")

    monkeypatch.setattr(fcp_live_canary, "_render_card", render)
    source = fcp_live_canary.create_source(workspace)

    tree = fcp_live_canary.ET.parse(source)
    assert tree.getroot().find(".//sequence").get("duration") == "8s"
    gaps = tree.getroot().findall(".//gap")
    assert len(gaps) == 1
    assert gaps[0].get("duration") == "1s"
    before = fcp_live_canary.hash_tree(workspace.source)
    (workspace.source / "unexpected-file").write_bytes(b"changed")
    assert fcp_live_canary.hash_tree(workspace.source) != before


def test_canary_program_is_valid_offline_and_covers_required_edits():
    program = fcp_live_canary.canary_program()

    program.validate_for({"duration_seconds": 8.0})
    operations = {(item.group, item.action) for item in program.operations}
    assert operations == {
        ("edit", "fill_gaps"),
        ("edit", "add_transition"),
        ("edit", "add_connected_clip"),
    }
    connected = {
        item.arguments["asset_id"]: item.arguments
        for item in program.operations
        if item.action == "add_connected_clip"
    }
    assert connected["r4"]["offset"] == "1s"
    assert connected["r4"]["duration"] == "2s"
    assert connected["r5"]["offset"] == "5s"
    assert program.changed_ranges == ((1.0, 3.0), (5.0, 6.0))


def test_pinned_modifier_chains_the_full_canary_program(monkeypatch, tmp_path):
    assert importlib.metadata.version("fcp-mcp-server") == "0.22.1"
    workspace = fcp_live_canary.create_canary_workspace(tmp_path / "canary")

    def render(destination, **_kwargs):
        destination.write_bytes(b"generated canary media")

    monkeypatch.setattr(fcp_live_canary, "_render_card", render)
    source = fcp_live_canary.create_source(workspace)
    modifier = FCPXMLModifier(str(source))
    program = fcp_live_canary.canary_program()
    for operation in program.operations:
        getattr(modifier, operation.action)(**operation.arguments)
    candidate = tmp_path / "candidate.fcpxml"
    modifier.save(str(candidate))

    checks = fcp_live_canary.candidate_structure_checks(candidate)
    transition = fcp_live_canary.ET.parse(candidate).find(".//transition")
    assert transition is not None
    assert fcp_live_canary._TRANSITION_OFFSET_SECONDS == 2.75
    assert fcp_live_canary._fcpxml_seconds(transition.get("offset")) == 82 / 30
    assert all(checks.values())


def _candidate_xml(
    *,
    title_offset: str = "1s",
    title_duration: str = "2s",
    transition_offset: str = "82/30s",
    transition_duration: str = "15/30s",
    transition_name: str = "Cross Dissolve",
    reaction_offset: str = "5s",
    reaction_duration: str = "1s",
) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.11">
  <resources>
    <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
    <asset id="r2" name="Red" start="0s" duration="3s" hasVideo="1" hasAudio="1" format="r1"/>
    <asset id="r3" name="Blue" start="0s" duration="5s" hasVideo="1" hasAudio="1" format="r1"/>
    <asset id="r4" name="Title" start="0s" duration="2s" hasVideo="1" hasAudio="0" format="r1"/>
    <asset id="r5" name="Reaction" start="0s" duration="1s" hasVideo="1" hasAudio="0" format="r1"/>
  </resources>
  <library><event name="Canary Event"><project name="Editor CLI Canary Source">
    <sequence format="r1" duration="8s"><spine>
      <asset-clip name="Red" ref="r2" offset="0s" start="0s" duration="3s"/>
      <asset-clip name="Canary Title" ref="r4" offset="{title_offset}" start="0s" duration="{title_duration}" lane="1"/>
      <transition name="{transition_name}" offset="{transition_offset}" duration="{transition_duration}"/>
      <asset-clip name="Blue" ref="r3" offset="3s" start="0s" duration="5s"/>
      <asset-clip name="Canary Reaction" ref="r5" offset="{reaction_offset}" start="0s" duration="{reaction_duration}" lane="2"/>
    </spine></sequence>
  </project></event></library>
</fcpxml>'''


def _write_image(
    path: Path, color: tuple[int, int, int], *, title: bool = False
) -> None:
    image = fcp_live_canary.Image.new("RGB", (128, 72), color)
    if title:
        fcp_live_canary.ImageDraw.Draw(image).rectangle((50, 30, 78, 42), fill="white")
    image.save(path)


def _write_evidence(
    root: Path,
    preview: Path,
    *,
    timestamps: tuple[float, ...] = (1.5, 3.0, 5.5),
    changed_ranges: tuple[tuple[float, float], ...] = ((1.0, 3.0), (5.0, 6.0)),
    stale: bool = False,
) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    frame_specs = {
        1.5: ((0, 0, 0), True),
        3.0: ((128, 0, 128), False),
        5.5: ((160, 0, 160), False),
    }
    frames = []
    for index, timestamp in enumerate(timestamps, 1):
        color, title = frame_specs[timestamp]
        frame = root / f"frame-{index}.png"
        _write_image(frame, color, title=title)
        frames.append({"path": str(frame), "timestamp_seconds": timestamp})
    digest = "0" if stale else hashlib.sha256(preview.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "preview": {"path": str(preview), "sha256": digest},
                "frames": frames,
                "changed_ranges": changed_ranges,
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize(
    ("override", "failed_check"),
    [
        (
            {
                "title_offset": _expected()["failure_cases"]["wrong_offsets"][
                    "title_offset"
                ]
            },
            "title_visible",
        ),
        (
            {
                "reaction_duration": _expected()["failure_cases"]["wrong_durations"][
                    "reaction_duration"
                ]
            },
            "reaction_insert_visible",
        ),
        (
            {
                "transition_offset": _expected()["failure_cases"][
                    "wrong_transition_placement"
                ]["transition_offset"]
            },
            "transition_visible",
        ),
        (
            {
                "transition_name": _expected()["failure_cases"][
                    "wrong_transition_type"
                ]["transition_name"]
            },
            "transition_visible",
        ),
    ],
)
def test_candidate_structure_rejects_wrong_canary_placement(
    tmp_path, override, failed_check
):
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(_candidate_xml(**override), encoding="utf-8")

    checks = fcp_live_canary.candidate_structure_checks(candidate)

    assert checks[failed_check] is False
    assert all(type(value) is bool for value in checks.values())


def test_rendered_watch_checks_reject_missing_frames_stale_and_unreadable_preview(
    tmp_path,
):
    expected = _expected()["failure_cases"]
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    ranges = ((1.0, 3.0), (5.0, 6.0))

    missing = _write_evidence(
        tmp_path / "missing",
        preview,
        timestamps=tuple(expected["missing_changed_range_frames"]["timestamps"]),
    )
    stale = _write_evidence(tmp_path / "stale", preview, stale=True)
    unreadable = _write_evidence(tmp_path / "unreadable", preview)
    (tmp_path / "unreadable" / "frame-1.png").write_bytes(
        expected["unreadable_preview"]["preview_bytes"].encode()
    )

    assert (
        fcp_live_canary.rendered_watch_checks(missing, preview, ranges)[
            "preview_watched"
        ]
        is False
    )
    assert (
        fcp_live_canary.rendered_watch_checks(stale, preview, ranges)["preview_fresh"]
        is False
    )
    assert (
        fcp_live_canary.rendered_watch_checks(unreadable, preview, ranges)[
            "title_visible"
        ]
        is False
    )


class _FakeFinalCut:
    def __init__(self, library: Path):
        self.identity = ProjectIdentity(
            library.stem, "Canary Event", "Editor CLI Canary Source", 8.0
        )
        self.opened_project = None

    async def active_projects(self):
        return (self.identity,)

    async def export_xml(self, _identity, destination):
        destination.write_text(_candidate_xml(), encoding="utf-8")

    async def inspect_xml(self, _path):
        return SimpleNamespace(
            project=self.identity.project, duration_seconds=8.0, frame_seconds=1 / 30
        )

    async def duplicate_project(self, _identity, _name):
        return None

    async def import_project(self, _path, _project_name):
        return None

    async def render_preview(self, _project_name, destination):
        destination.write_bytes(b"rendered canary preview")

    async def open_project(self, project_name):
        self.opened_project = project_name


class _FakeTimeline:
    def __init__(self, candidate_xml: str, source: Path, mutate_source: bool):
        self.candidate_xml = candidate_xml
        self.source = source
        self.mutate_source = mutate_source

    async def analyze(self, _source):
        return {"duration_seconds": 8.0, "clips": 2, "gaps": 1}

    async def apply(self, _source, _program, destination):
        if self.mutate_source:
            (self.source / "mutated").write_bytes(b"source changed")
        destination.write_text(self.candidate_xml, encoding="utf-8")
        return destination


class _FakeWatch:
    def analyze(self, preview, out, changed_ranges):
        return SimpleNamespace(manifest=_write_evidence(out, preview))


class _FakeBootstrap:
    def __init__(self, *_args, **_kwargs):
        pass

    async def call(self, _tool, _arguments):
        return {}


async def _run_controller_backed_canary(
    monkeypatch,
    tmp_path,
    *,
    candidate_xml: str | None = None,
    mutate_source: bool = False,
    technical_valid: bool = True,
):
    workspace = fcp_live_canary.create_canary_workspace(tmp_path / "canary")
    source_xml = workspace.source / "source.fcpxml"
    source_xml.write_text(_candidate_xml(), encoding="utf-8")
    source_hashes = fcp_live_canary.hash_tree(workspace.source)
    fcp = _FakeFinalCut(workspace.library)
    deps = ControllerDeps(
        sessions=SessionRepository(ControllerConfig(session_root=workspace.sessions)),
        fcp=fcp,
        timeline=_FakeTimeline(
            candidate_xml or _candidate_xml(), workspace.source, mutate_source
        ),
        watch=_FakeWatch(),
    )
    controller = EditSessionController(deps)
    monkeypatch.setattr(
        fcp_live_canary,
        "build_services",
        lambda _config, doctor: SimpleNamespace(
            session=SimpleNamespace(controller=controller)
        ),
    )
    monkeypatch.setattr(fcp_live_canary, "FCPXMLMCPClient", _FakeBootstrap)

    async def wait_for_canary(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fcp_live_canary, "_wait_for_project", wait_for_canary)
    monkeypatch.setattr(
        fcp_live_canary,
        "inspect_preview",
        lambda *_args, **_kwargs: ReviewReport(
            required={"technical": technical_valid}, observations=()
        ),
    )
    result = await fcp_live_canary.run_canary(
        workspace,
        {"ready": True},
        source_xml,
        fcp_live_canary.canary_program(),
        source_hashes,
    )
    return result, fcp


@pytest.mark.anyio
async def test_run_canary_records_all_checks_before_opening_candidate(
    monkeypatch, tmp_path
):
    result, fcp = await _run_controller_backed_canary(monkeypatch, tmp_path)

    assert result["state"] == "ready"
    assert result["required_checks"] == _expected()["passing_result"]
    assert fcp.opened_project == "Editor CLI Canary Source - AI Pass 1"


@pytest.mark.anyio
async def test_run_canary_fails_closed_when_source_tree_changes(monkeypatch, tmp_path):
    result, fcp = await _run_controller_backed_canary(
        monkeypatch, tmp_path, mutate_source=True
    )

    assert result["state"] == "correct"
    assert result["required_checks"]["source_unchanged"] is False
    assert fcp.opened_project is None


@pytest.mark.anyio
async def test_run_canary_does_not_open_wrong_candidate(monkeypatch, tmp_path):
    result, fcp = await _run_controller_backed_canary(
        monkeypatch, tmp_path, candidate_xml=_candidate_xml(title_offset="5s")
    )

    assert result["state"] == "correct"
    assert result["required_checks"]["title_visible"] is False
    assert fcp.opened_project is None


@pytest.mark.anyio
async def test_run_canary_does_not_open_an_unreadable_preview(monkeypatch, tmp_path):
    result, fcp = await _run_controller_backed_canary(
        monkeypatch,
        tmp_path,
        technical_valid=_expected()["failure_cases"]["unreadable_preview"]["technical"],
    )

    assert result["state"] == "correct"
    assert result["required_checks"]["preview_rendered"] is False
    assert fcp.opened_project is None


def test_all_required_checks_requires_exact_booleans_and_ready_state():
    passing = _expected()["passing_result"]
    assert fcp_live_canary._all_required_checks_pass(
        {"state": "ready", "required_checks": passing}
    )
    assert not fcp_live_canary._all_required_checks_pass(
        {"state": "ready", "required_checks": {**passing, "title_visible": 1}}
    )
    assert not fcp_live_canary._all_required_checks_pass(
        {"state": "correct", "required_checks": passing}
    )


def test_main_returns_failure_when_run_does_not_reach_ready(monkeypatch, tmp_path):
    async def run(_workspace, _report, _source, _program, _source_hashes):
        return {
            "state": "correct",
            "required_checks": dict(_expected()["passing_result"]),
        }

    monkeypatch.setattr(fcp_live_canary, "require_device", lambda: {"ready": True})
    monkeypatch.setattr(
        fcp_live_canary,
        "create_source",
        lambda workspace: workspace.source / "source.fcpxml",
    )
    monkeypatch.setattr(fcp_live_canary, "run_canary", run)
    workspace = tmp_path / "main-canary"
    original_create = fcp_live_canary.create_canary_workspace

    def create(root):
        created = original_create(root)
        (created.source / "source.fcpxml").write_text(
            _candidate_xml(), encoding="utf-8"
        )
        return created

    monkeypatch.setattr(fcp_live_canary, "create_canary_workspace", create)

    assert fcp_live_canary.main(["--workspace", str(workspace)]) == 1
    saved = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert saved["required_checks"]["source_unchanged"] is True
