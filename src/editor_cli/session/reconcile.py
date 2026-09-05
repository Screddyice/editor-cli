"""Fail-closed reconciliation for interrupted external actions."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from editor_cli.session.models import ExternalAction, ProjectIdentity


class ReconciliationError(RuntimeError):
    """Raised when an interrupted action cannot be proved complete."""


class ReconciliationControl(Protocol):
    async def active_projects(self) -> Sequence[ProjectIdentity]: ...

    async def inspect_xml(self, path: Path): ...


_ACTION_KIND = {
    "commandpost.export": "export_xml",
    "finalcut.export": "export_xml",
    "finalcut.export_xml": "export_xml",
    "export_xml": "export_xml",
    "commandpost.duplicate": "duplicate_project",
    "finalcut.duplicate": "duplicate_project",
    "finalcut.duplicate_project": "duplicate_project",
    "duplicate_project": "duplicate_project",
    "finalcut.import_xml": "import_xml",
    "import_xml": "import_xml",
    "finalcut.share_preview": "share_preview",
    "share_preview": "share_preview",
    "finalcut.open_project": "open_project",
    "open_project": "open_project",
    "internet.download": "download",
    "download": "download",
}
_IDENTITY_FIELDS = {"library", "event", "project", "duration_seconds"}
_HASH = re.compile(r"[0-9a-f]{64}")


async def reconcile_external_action(
    action: ExternalAction,
    control: ReconciliationControl,
    session_root: Path,
) -> dict[str, Any]:
    """Return a receipt only when current state proves the action completed."""

    if action.status != "pending":
        raise ReconciliationError("Only pending external actions can be reconciled")
    try:
        kind = _ACTION_KIND[action.action]
        expected = _plain_dict(action.expected, "expected data")
        if set(expected) != {"identity", "idempotency"}:
            raise ValueError
        identity_data = _plain_dict(expected["identity"], "expected identity")
        idempotency = _plain_dict(expected["idempotency"], "idempotency data")
        arguments = _plain_dict(action.arguments, "arguments")
        root = session_root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError

        if kind == "download":
            return _reconcile_download(arguments, identity_data, idempotency, root)

        identity = _project_identity(identity_data)
        await _require_exact_active(control, identity)
        if kind == "export_xml":
            return await _reconcile_export(
                control, arguments, idempotency, identity, root
            )
        if kind == "import_xml":
            _reconcile_import_source(arguments, idempotency, identity, root)
        elif kind == "share_preview":
            return _reconcile_share(arguments, idempotency, identity, root)
        elif kind in {"duplicate_project", "open_project"}:
            _require_project_name(arguments, idempotency, identity, kind)
        else:  # pragma: no cover - guarded by the action table
            raise ValueError
        result: dict[str, Any] = {"identity": asdict(identity)}
        if kind == "duplicate_project":
            result["preserved_name"] = identity.project
        return result
    except ReconciliationError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ReconciliationError(
            f"External action journal data is malformed for {action.action}"
        ) from exc


def _plain_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ReconciliationError(f"External action {label} is malformed")
    return dict(value)


def _project_identity(value: dict[str, Any]) -> ProjectIdentity:
    if set(value) != _IDENTITY_FIELDS:
        raise ValueError
    for field in ("library", "event", "project"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError
    duration = value["duration_seconds"]
    if type(duration) is not float or not math.isfinite(duration) or duration < 0:
        raise ValueError
    return ProjectIdentity(
        library=value["library"],
        event=value["event"],
        project=value["project"],
        duration_seconds=float(duration),
    )


async def _require_exact_active(
    control: ReconciliationControl, expected: ProjectIdentity
) -> None:
    projects = tuple(await control.active_projects())
    if len(projects) != 1 or projects[0] != expected:
        raise ReconciliationError(
            "Interrupted action does not have one exact active project identity"
        )


async def _reconcile_export(
    control: ReconciliationControl,
    arguments: dict[str, Any],
    idempotency: dict[str, Any],
    identity: ProjectIdentity,
    root: Path,
) -> dict[str, Any]:
    if set(arguments) != {"project", "destination"}:
        raise ValueError
    if set(idempotency) not in ({"destination"}, {"destination", "sha256"}):
        raise ValueError
    if arguments["project"] != identity.project:
        raise ValueError
    destination = _exact_path(
        arguments["destination"], idempotency.get("destination"), root
    )
    if not destination.is_file():
        raise ReconciliationError("Interrupted export did not create its exact output")
    parsed = await control.inspect_xml(destination)
    if (
        getattr(parsed, "project", None) != identity.project
        or getattr(parsed, "duration_seconds", None) != identity.duration_seconds
    ):
        raise ReconciliationError("Interrupted export XML identity does not match")
    digest = _file_sha256(destination)
    expected_hash = idempotency.get("sha256")
    if expected_hash is not None and _digest(expected_hash) != digest:
        raise ReconciliationError("Interrupted export output hash does not match")
    return {"identity": asdict(identity), "path": str(destination), "sha256": digest}


def _reconcile_import_source(
    arguments: dict[str, Any],
    idempotency: dict[str, Any],
    identity: ProjectIdentity,
    root: Path,
) -> None:
    if set(arguments) != {"path", "identity"}:
        raise ValueError
    if set(idempotency) != {"project_name", "candidate_sha256"}:
        raise ValueError
    if (
        _project_identity(_plain_dict(arguments["identity"], "arguments identity"))
        != identity
    ):
        raise ValueError
    source = _contained_path(arguments["path"], root)
    expected_hash = _digest(idempotency.get("candidate_sha256"))
    if idempotency.get("project_name") != identity.project:
        raise ValueError
    if not source.is_file() or _file_sha256(source) != expected_hash:
        raise ReconciliationError("Interrupted import candidate hash does not match")


def _reconcile_share(
    arguments: dict[str, Any],
    idempotency: dict[str, Any],
    identity: ProjectIdentity,
    root: Path,
) -> dict[str, Any]:
    if set(arguments) != {"identity", "destination"}:
        raise ValueError
    if set(idempotency) != {"destination", "candidate_sha256"}:
        raise ValueError
    if (
        _project_identity(_plain_dict(arguments["identity"], "arguments identity"))
        != identity
    ):
        raise ValueError
    candidate_hash = _digest(idempotency.get("candidate_sha256"))
    if _unique_file_with_hash(root / "candidates", candidate_hash) is None:
        raise ReconciliationError(
            "Interrupted share candidate hash does not match one exact candidate"
        )
    destination = _exact_path(
        arguments["destination"], idempotency.get("destination"), root
    )
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise ReconciliationError("Interrupted share did not create its exact output")
    return {
        "kind": "final_cut_share",
        "identity": asdict(identity),
        "output": str(destination),
        "sha256": _file_sha256(destination),
    }


def _require_project_name(
    arguments: dict[str, Any],
    idempotency: dict[str, Any],
    identity: ProjectIdentity,
    kind: str,
) -> None:
    if set(idempotency) != {"project_name"}:
        raise ValueError
    if idempotency.get("project_name") != identity.project:
        raise ValueError
    if kind == "duplicate_project":
        if set(arguments) != {"project", "preserved_name"}:
            raise ValueError
        if (
            not isinstance(arguments["project"], str)
            or not arguments["project"].strip()
            or arguments["project"] == identity.project
            or arguments["preserved_name"] != identity.project
        ):
            raise ValueError
    elif set(arguments) == {"identity"}:
        if (
            _project_identity(_plain_dict(arguments["identity"], "arguments identity"))
            != identity
        ):
            raise ValueError
    elif set(arguments) == {"project_name"}:
        if arguments["project_name"] != identity.project:
            raise ValueError
    else:
        raise ValueError


def _reconcile_download(
    arguments: dict[str, Any],
    identity: dict[str, Any],
    idempotency: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    if set(idempotency) != {"source_url", "sha256"}:
        raise ValueError
    if set(arguments) not in (
        {"url", "path"},
        {"url", "purpose", "staging_path"},
    ):
        raise ValueError
    if "purpose" in arguments and (
        not isinstance(arguments["purpose"], str) or not arguments["purpose"].strip()
    ):
        raise ValueError
    source_url = idempotency.get("source_url")
    if (
        set(identity) != {"source_url"}
        or identity.get("source_url") != source_url
        or arguments.get("url") != source_url
        or not isinstance(source_url, str)
        or not source_url.startswith("https://")
    ):
        raise ValueError
    expected_hash = _digest(idempotency.get("sha256"))
    supplied = arguments.get("path") or arguments.get("staging_path")
    candidates: list[Path] = []
    if supplied is not None:
        candidate = _contained_path(supplied, root)
        if candidate.is_file() and _file_sha256(candidate) == expected_hash:
            candidates.append(candidate)
    if not candidates:
        for candidate in root.rglob("*"):
            if (
                candidate.is_file()
                and candidate.name
                not in {
                    "journal.jsonl",
                    "provenance.jsonl",
                    "state.json",
                    ".lock",
                    ".session.lock",
                }
                and _file_sha256(candidate) == expected_hash
            ):
                candidates.append(candidate.resolve())
    unique = tuple(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise ReconciliationError(
            "Interrupted download does not have one exact source and output hash"
        )
    return {"path": str(unique[0]), "sha256": expected_hash, "source_url": source_url}


def _unique_file_with_hash(root: Path, digest: str) -> Path | None:
    if not root.is_dir():
        return None
    matches = [
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and _file_sha256(path) == digest
    ]
    unique = tuple(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def _exact_path(actual: Any, expected: Any, root: Path) -> Path:
    path = _contained_path(actual, root)
    if path != _contained_path(expected, root):
        raise ValueError
    return path


def _contained_path(value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError
    path = Path(value).expanduser().resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError
    return path


def _digest(value: Any) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
