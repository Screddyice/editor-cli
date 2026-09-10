import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from editor_cli.session.models import ExternalAction, ProjectIdentity
from editor_cli.session.reconcile import (
    ReconciliationError,
    reconcile_external_action,
    validate_completed_external_action,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


IDENTITY = ProjectIdentity("Library", "Event", "Demo", 12.0)


class FakeFinalCut:
    def __init__(self, projects=(IDENTITY,)):
        self.projects = tuple(projects)
        self.probes = 0

    async def active_projects(self):
        self.probes += 1
        return self.projects

    async def inspect_xml(self, _path):
        return SimpleNamespace(
            project=IDENTITY.project,
            duration_seconds=IDENTITY.duration_seconds,
            frame_seconds=1 / 30,
        )


def action(name, arguments, *, identity=None, idempotency=None):
    return ExternalAction(
        token="token",
        action=name,
        arguments=arguments,
        expected={
            "identity": identity
            or {
                "library": IDENTITY.library,
                "event": IDENTITY.event,
                "project": IDENTITY.project,
                "duration_seconds": IDENTITY.duration_seconds,
            },
            "idempotency": idempotency or {"project_name": IDENTITY.project},
        },
        status="pending",
    )


def write_share_receipt(destination):
    Path(str(destination) + ".receipt.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "final_cut_share",
                "identity": IDENTITY.__dict__,
                "output": str(destination.resolve()),
                "sha256": sha256(destination.read_bytes()).hexdigest(),
                "size_bytes": destination.stat().st_size,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name",
    [
        "finalcut.duplicate_project",
        "finalcut.import_xml",
        "finalcut.open_project",
    ],
)
async def test_project_reconcilers_require_one_exact_active_identity(tmp_path, name):
    source = tmp_path / "candidate.fcpxml"
    source.write_bytes(b"candidate")
    arguments = {"identity": IDENTITY.__dict__}
    idempotency = {"project_name": IDENTITY.project}
    if name == "finalcut.duplicate_project":
        arguments = {"project": "Source", "preserved_name": IDENTITY.project}
    elif name == "finalcut.import_xml":
        arguments["path"] = str(source)
        idempotency["candidate_sha256"] = sha256(b"candidate").hexdigest()

    result = await reconcile_external_action(
        action(name, arguments, idempotency=idempotency),
        FakeFinalCut(),
        tmp_path,
    )

    expected = {"identity": IDENTITY.__dict__}
    if name == "finalcut.duplicate_project":
        expected["preserved_name"] = IDENTITY.project
    elif name == "finalcut.import_xml":
        expected.update(
            {
                "path": str(source),
                "sha256": sha256(b"candidate").hexdigest(),
                "size_bytes": len(b"candidate"),
            }
        )
    assert result == expected


@pytest.mark.anyio
async def test_export_reconciler_binds_exact_identity_path_and_hash(tmp_path):
    destination = tmp_path / "source.fcpxml"
    destination.write_bytes(b"source")

    result = await reconcile_external_action(
        action(
            "finalcut.export_xml",
            {"destination": str(destination), "project": IDENTITY.project},
            idempotency={"destination": str(destination)},
        ),
        FakeFinalCut(),
        tmp_path,
    )

    assert result == {
        "identity": IDENTITY.__dict__,
        "path": str(destination),
        "sha256": sha256(b"source").hexdigest(),
        "size_bytes": len(b"source"),
    }


@pytest.mark.anyio
async def test_share_reconciler_binds_exact_identity_destination_and_hash(tmp_path):
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / "pass-01.fcpxml"
    candidate.write_bytes(b"candidate")
    destination = tmp_path / "preview.mp4"
    destination.write_bytes(b"preview")
    write_share_receipt(destination)

    result = await reconcile_external_action(
        action(
            "finalcut.share_preview",
            {"identity": IDENTITY.__dict__, "destination": str(destination)},
            idempotency={
                "destination": str(destination),
                "candidate_path": str(candidate),
                "candidate_sha256": sha256(b"candidate").hexdigest(),
            },
        ),
        FakeFinalCut(),
        tmp_path,
    )

    assert result == {
        "kind": "final_cut_share",
        "identity": IDENTITY.__dict__,
        "output": str(destination),
        "sha256": sha256(b"preview").hexdigest(),
        "size_bytes": len(b"preview"),
    }


@pytest.mark.anyio
async def test_share_reconciler_rejects_missing_exact_candidate_hash(tmp_path):
    candidate = tmp_path / "candidates" / "pass-01.fcpxml"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    destination = tmp_path / "preview.mp4"
    destination.write_bytes(b"preview")
    write_share_receipt(destination)
    pending = action(
        "finalcut.share_preview",
        {"identity": IDENTITY.__dict__, "destination": str(destination)},
        idempotency={
            "destination": str(destination),
            "candidate_path": str(candidate),
            "candidate_sha256": "a" * 64,
        },
    )

    with pytest.raises(ReconciliationError, match="candidate hash"):
        await reconcile_external_action(pending, FakeFinalCut(), tmp_path)


@pytest.mark.anyio
async def test_share_reconciler_rejects_output_without_native_completion_receipt(
    tmp_path,
):
    candidate = tmp_path / "candidates" / "pass-01.fcpxml"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    destination = tmp_path / "preview.mp4"
    destination.write_bytes(b"partial export")
    pending = action(
        "finalcut.share_preview",
        {"identity": IDENTITY.__dict__, "destination": str(destination)},
        idempotency={
            "destination": str(destination),
            "candidate_path": str(candidate),
            "candidate_sha256": sha256(candidate.read_bytes()).hexdigest(),
        },
    )

    with pytest.raises(ReconciliationError, match="native completion receipt"):
        await reconcile_external_action(pending, FakeFinalCut(), tmp_path)


@pytest.mark.anyio
async def test_share_reconciler_does_not_substitute_same_hash_candidate(tmp_path):
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    expected = candidates / "pass-01.fcpxml"
    neighbor = candidates / "pass-02.fcpxml"
    neighbor.write_bytes(b"candidate")
    destination = tmp_path / "preview.mp4"
    destination.write_bytes(b"preview")
    write_share_receipt(destination)

    with pytest.raises(ReconciliationError, match="candidate hash and path"):
        await reconcile_external_action(
            action(
                "finalcut.share_preview",
                {"identity": IDENTITY.__dict__, "destination": str(destination)},
                idempotency={
                    "destination": str(destination),
                    "candidate_path": str(expected),
                    "candidate_sha256": sha256(neighbor.read_bytes()).hexdigest(),
                },
            ),
            FakeFinalCut(),
            tmp_path,
        )


@pytest.mark.anyio
async def test_download_reconciler_requires_exact_source_path_and_hash(tmp_path):
    asset = tmp_path / "assets" / "reaction.mp4"
    asset.parent.mkdir()
    asset.write_bytes(b"asset")
    digest = sha256(b"asset").hexdigest()
    source_url = "https://example.com/reaction.mp4"

    result = await reconcile_external_action(
        action(
            "internet.download",
            {"url": source_url, "path": str(asset)},
            identity={"source_url": source_url},
            idempotency={"source_url": source_url, "sha256": digest},
        ),
        FakeFinalCut(),
        tmp_path,
    )

    assert result == {
        "path": str(asset),
        "sha256": digest,
        "size_bytes": len(b"asset"),
        "source_url": source_url,
    }


@pytest.mark.anyio
async def test_download_reconciler_does_not_substitute_same_hash_neighbor(tmp_path):
    missing = tmp_path / "assets" / "expected.mp4"
    neighbor = tmp_path / "assets" / "neighbor.mp4"
    neighbor.parent.mkdir()
    neighbor.write_bytes(b"same bytes")
    digest = sha256(b"same bytes").hexdigest()
    source_url = "https://example.com/reaction.mp4"

    with pytest.raises(ReconciliationError, match="exact source"):
        await reconcile_external_action(
            action(
                "internet.download",
                {"url": source_url, "path": str(missing)},
                identity={"source_url": source_url},
                idempotency={"source_url": source_url, "sha256": digest},
            ),
            FakeFinalCut(),
            tmp_path,
        )


@pytest.mark.anyio
async def test_completed_receipt_requires_exact_result_evidence(tmp_path):
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / "pass-01.fcpxml"
    candidate.write_bytes(b"candidate")
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    write_share_receipt(preview)
    completed = replace(
        action(
            "finalcut.share_preview",
            {"identity": IDENTITY.__dict__, "destination": str(preview)},
            idempotency={
                "destination": str(preview),
                "candidate_path": str(candidate),
                "candidate_sha256": sha256(b"candidate").hexdigest(),
            },
        ),
        status="complete",
    )
    exact = {
        "kind": "final_cut_share",
        "identity": IDENTITY.__dict__,
        "output": str(preview),
        "sha256": sha256(b"preview").hexdigest(),
        "size_bytes": len(b"preview"),
    }

    assert (
        await validate_completed_external_action(
            completed, exact, FakeFinalCut(), tmp_path
        )
        == exact
    )
    for malformed in (
        {key: value for key, value in exact.items() if key != "sha256"},
        {**exact, "output": str(tmp_path / "other.mp4")},
        {**exact, "kind": "other"},
        {**exact, "size_bytes": 999},
    ):
        with pytest.raises(ReconciliationError, match="result evidence"):
            await validate_completed_external_action(
                completed, malformed, FakeFinalCut(), tmp_path
            )


@pytest.mark.anyio
async def test_reconciler_fails_closed_for_ambiguous_or_malformed_evidence(tmp_path):
    with pytest.raises(ReconciliationError, match="exact active project"):
        await reconcile_external_action(
            action("finalcut.open_project", {"identity": IDENTITY.__dict__}),
            FakeFinalCut((IDENTITY, IDENTITY)),
            tmp_path,
        )

    malformed = action(
        "finalcut.share_preview",
        {"identity": IDENTITY.__dict__},
        idempotency={"destination": str(tmp_path / "missing.mp4")},
    )
    with pytest.raises(ReconciliationError, match="malformed"):
        await reconcile_external_action(malformed, FakeFinalCut(), tmp_path)

    extra_field = action(
        "finalcut.open_project",
        {"identity": IDENTITY.__dict__},
        idempotency={"project_name": IDENTITY.project, "retry": True},
    )
    with pytest.raises(ReconciliationError, match="malformed"):
        await reconcile_external_action(extra_field, FakeFinalCut(), tmp_path)


@pytest.mark.anyio
async def test_import_reconciler_rejects_changed_candidate_hash(tmp_path):
    source = tmp_path / "candidate.fcpxml"
    source.write_bytes(b"changed")
    pending = action(
        "finalcut.import_xml",
        {"path": str(source), "identity": IDENTITY.__dict__},
        idempotency={
            "project_name": IDENTITY.project,
            "candidate_sha256": "0" * 64,
        },
    )

    with pytest.raises(ReconciliationError, match="hash"):
        await reconcile_external_action(pending, FakeFinalCut(), tmp_path)
