"""Typed, fail-closed client for the native Final Cut bridge."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from editor_cli.session.models import ProjectIdentity

PROTOCOL_VERSION = 1
MAXIMUM_RESPONSE_BYTES = 1_048_576


class NativeFinalCutError(RuntimeError):
    """Raised when the native helper violates or rejects its fixed protocol."""


@dataclass(frozen=True)
class BlockingDialog:
    role: str
    title: str


@dataclass(frozen=True)
class NativeProbe:
    protocol_version: int
    helper_sha256: str
    final_cut_bundle_id: str
    final_cut_version: str
    accessibility: bool
    automation: bool
    ready: bool
    dialogs: tuple[BlockingDialog, ...]
    library_names: tuple[str, ...] = ()
    active_project: ProjectIdentity | None = None


@dataclass(frozen=True)
class ExportReceipt:
    kind: str
    project: ProjectIdentity
    output: Path


@dataclass(frozen=True)
class ShareReceipt:
    kind: str
    project: ProjectIdentity
    output: Path


Runner = Callable[..., subprocess.CompletedProcess[str]]


class NativeFinalCutClient:
    def __init__(
        self,
        executable: Path,
        *,
        runner: Runner = subprocess.run,
        action_timeout: float = 120,
    ):
        if (
            isinstance(action_timeout, bool)
            or not isinstance(action_timeout, (int, float))
            or not math.isfinite(action_timeout)
            or action_timeout <= 0
            or action_timeout > 3_600
        ):
            raise ValueError(
                "Native Final Cut action timeout must be between 0 and 3,600 seconds"
            )
        self._executable = executable.expanduser().resolve()
        self._runner = runner
        self._action_timeout = action_timeout

    def probe(self, session_root: Path) -> NativeProbe:
        result = self._invoke("probe", {}, session_root)
        _require_keys(
            result,
            {
                "protocolVersion",
                "bundleIdentifier",
                "version",
                "ready",
                "accessibilityTrusted",
                "automationAuthorized",
                "libraryNames",
                "activeProject",
                "dialogs",
            },
            "probe result",
        )
        libraries = result["libraryNames"]
        if not isinstance(libraries, list) or any(
            not isinstance(item, str) or not item for item in libraries
        ):
            raise NativeFinalCutError(
                "Native Final Cut probe returned invalid library names"
            )
        raw_active = result["activeProject"]
        active = None if raw_active is None else _decode_identity(raw_active)
        return NativeProbe(
            protocol_version=PROTOCOL_VERSION,
            helper_sha256=self._helper_sha256(),
            final_cut_bundle_id=_string(
                result["bundleIdentifier"], "bundle identifier"
            ),
            final_cut_version=_string(result["version"], "Final Cut version"),
            accessibility=_boolean(
                result["accessibilityTrusted"], "Accessibility state"
            ),
            automation=_boolean(result["automationAuthorized"], "Automation state"),
            ready=_boolean(result["ready"], "ready state"),
            dialogs=_decode_dialogs(result["dialogs"]),
            library_names=tuple(libraries),
            active_project=active,
        )

    def duplicate_project(
        self, identity: ProjectIdentity, name: str, session_root: Path
    ) -> ProjectIdentity:
        result = self._invoke(
            "duplicate_project",
            {
                "expected": _encode_identity(identity),
                "name": name,
                "timeout": self._action_timeout,
            },
            session_root,
        )
        expected = ProjectIdentity(
            identity.library,
            identity.event,
            name,
            identity.duration_seconds,
        )
        return self._bound_project(result, expected)

    def export_xml(
        self, identity: ProjectIdentity, destination: Path, session_root: Path
    ) -> ExportReceipt:
        root = _session_root(session_root)
        output = _contained_path(destination, root)
        result = self._invoke(
            "export_xml",
            {
                "expected": _encode_identity(identity),
                "output": str(output),
                "timeout": self._action_timeout,
            },
            root,
        )
        return self._export_receipt(result, identity, root, output)

    def import_xml(
        self, identity: ProjectIdentity, source: Path, session_root: Path
    ) -> ProjectIdentity:
        root = _session_root(session_root)
        input_path = _contained_path(source, root)
        result = self._invoke(
            "import_xml",
            {
                "expected": _encode_identity(identity),
                "source": str(input_path),
                "timeout": self._action_timeout,
            },
            root,
        )
        return self._bound_project(result, identity)

    def open_project(
        self, identity: ProjectIdentity, session_root: Path
    ) -> ProjectIdentity:
        result = self._invoke(
            "open_project",
            {
                "expected": _encode_identity(identity),
                "timeout": self._action_timeout,
            },
            session_root,
        )
        return self._bound_project(result, identity)

    def share_preview(
        self, identity: ProjectIdentity, destination: Path, session_root: Path
    ) -> ShareReceipt:
        root = _session_root(session_root)
        output = _contained_path(destination, root)
        result = self._invoke(
            "share_preview",
            {
                "expected": _encode_identity(identity),
                "output": str(output),
                "timeout": self._action_timeout,
            },
            root,
        )
        _require_keys(
            result,
            {"protocolVersion", "kind", "project", "output"},
            "share result",
        )
        if result["kind"] != "final_cut_share":
            raise NativeFinalCutError(
                "Native Final Cut share returned the wrong receipt kind"
            )
        project = _require_identity(result["project"], identity)
        receipt_output = _receipt_path(result["output"], root, output)
        return ShareReceipt("final_cut_share", project, receipt_output)

    def inspect_dialogs(self, session_root: Path) -> tuple[BlockingDialog, ...]:
        result = self._invoke("inspect_dialogs", {}, session_root)
        _require_keys(result, {"protocolVersion", "dialogs"}, "dialog result")
        return _decode_dialogs(result["dialogs"])

    def _invoke(
        self, action: str, payload: dict[str, Any], session_root: Path
    ) -> dict[str, Any]:
        root = _session_root(session_root)
        request = json.dumps(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "action": action,
                "sessionRoot": str(root),
                "payload": payload,
            },
            allow_nan=False,
            separators=(",", ":"),
        )
        try:
            completed = self._runner(
                [str(self._executable)],
                input=request,
                text=True,
                capture_output=True,
                timeout=self._action_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NativeFinalCutError("Native Final Cut action timed out") from exc
        except UnicodeError as exc:
            raise NativeFinalCutError(
                "Native Final Cut helper returned an invalid text response"
            ) from exc
        except OSError as exc:
            raise NativeFinalCutError(
                f"Unable to start native Final Cut helper: {exc}"
            ) from exc

        response = completed.stdout
        if not isinstance(response, str):
            raise NativeFinalCutError(
                "Native Final Cut helper returned a non-text response"
            )
        if len(response.encode("utf-8")) > MAXIMUM_RESPONSE_BYTES:
            raise NativeFinalCutError(
                "Native Final Cut response exceeds the 1 MiB limit"
            )
        decoded = _decode_response(response)
        ok = decoded["ok"]
        if not ok:
            if completed.returncode == 0:
                raise NativeFinalCutError(
                    "Native Final Cut helper returned an error with exit status 0"
                )
            raise NativeFinalCutError(_string(decoded["error"], "helper error"))
        if completed.returncode != 0:
            raise NativeFinalCutError(
                f"Native Final Cut helper exited with status {completed.returncode} after success"
            )
        result = decoded["result"]
        if not isinstance(result, dict):
            raise NativeFinalCutError(
                "Native Final Cut response result must be an object"
            )
        version = result.get("protocolVersion")
        if type(version) is not int or version != PROTOCOL_VERSION:
            raise NativeFinalCutError(
                "Native Final Cut response protocol version is invalid"
            )
        return result

    def _bound_project(
        self, result: dict[str, Any], expected: ProjectIdentity
    ) -> ProjectIdentity:
        _require_keys(result, {"protocolVersion", "project"}, "project result")
        return _require_identity(result["project"], expected)

    def _export_receipt(
        self,
        result: dict[str, Any],
        expected: ProjectIdentity,
        root: Path,
        output: Path,
    ) -> ExportReceipt:
        _require_keys(
            result,
            {"protocolVersion", "kind", "project", "output"},
            "export result",
        )
        if result["kind"] != "fcpxml_export":
            raise NativeFinalCutError(
                "Native Final Cut export returned the wrong receipt kind"
            )
        project = _require_identity(result["project"], expected)
        receipt_output = _receipt_path(result["output"], root, output)
        return ExportReceipt("fcpxml_export", project, receipt_output)

    def _helper_sha256(self) -> str:
        digest = hashlib.sha256()
        try:
            with self._executable.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise NativeFinalCutError(
                f"Unable to hash native Final Cut helper: {exc}"
            ) from exc
        return digest.hexdigest()


def _decode_response(stdout: str) -> dict[str, Any]:
    if not stdout.endswith("\n") or stdout.count("\n") != 1:
        raise NativeFinalCutError(
            "Native Final Cut response must contain one JSON object"
        )
    body = stdout[:-1]
    if not body or body.strip() != body:
        raise NativeFinalCutError("Native Final Cut response contains extra data")
    try:
        decoded = json.loads(
            body,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_non_json_number,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError) as exc:
        raise NativeFinalCutError(
            "Native Final Cut response is not valid JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise NativeFinalCutError("Native Final Cut response must be an object")
    ok = decoded.get("ok")
    if type(ok) is not bool:
        raise NativeFinalCutError("Native Final Cut response has an invalid ok value")
    expected_keys = {"ok", "result"} if ok else {"ok", "error"}
    _require_keys(decoded, expected_keys, "response")
    return decoded


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _session_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.is_absolute() or root == Path(root.anchor):
        raise NativeFinalCutError("Native Final Cut session root is invalid")
    return root


def _contained_path(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise NativeFinalCutError("Native Final Cut path is outside the session root")
    return resolved


def _receipt_path(value: Any, root: Path, expected: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise NativeFinalCutError("Native Final Cut receipt output is invalid")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise NativeFinalCutError("Native Final Cut receipt output must be absolute")
    output = _contained_path(candidate, root)
    if output != expected:
        raise NativeFinalCutError("Native Final Cut receipt output changed")
    return output


def _encode_identity(identity: ProjectIdentity) -> dict[str, object]:
    _validate_identity(identity)
    return {
        "library": identity.library,
        "event": identity.event,
        "project": identity.project,
        "duration_seconds": identity.duration_seconds,
    }


def _decode_identity(value: Any) -> ProjectIdentity:
    if not isinstance(value, dict):
        raise NativeFinalCutError("Native Final Cut identity must be an object")
    _require_keys(
        value,
        {"library", "event", "project", "duration_seconds"},
        "identity",
    )
    duration = value["duration_seconds"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
    ):
        raise NativeFinalCutError("Native Final Cut identity duration is invalid")
    identity = ProjectIdentity(
        library=_string(value["library"], "identity library"),
        event=_string(value["event"], "identity event"),
        project=_string(value["project"], "identity project"),
        duration_seconds=float(duration),
    )
    _validate_identity(identity)
    return identity


def _validate_identity(identity: ProjectIdentity) -> None:
    if (
        not isinstance(identity.library, str)
        or not identity.library
        or not isinstance(identity.event, str)
        or not identity.event
        or not isinstance(identity.project, str)
        or not identity.project
        or isinstance(identity.duration_seconds, bool)
        or not isinstance(identity.duration_seconds, (int, float))
        or not math.isfinite(identity.duration_seconds)
    ):
        raise NativeFinalCutError("Native Final Cut identity is invalid")


def _require_identity(value: Any, expected: ProjectIdentity) -> ProjectIdentity:
    actual = _decode_identity(value)
    if actual != expected:
        raise NativeFinalCutError(
            "Native Final Cut response identity does not match the request"
        )
    return actual


def _decode_dialogs(value: Any) -> tuple[BlockingDialog, ...]:
    if not isinstance(value, list):
        raise NativeFinalCutError("Native Final Cut dialogs must be an array")
    dialogs: list[BlockingDialog] = []
    for item in value:
        if not isinstance(item, dict):
            raise NativeFinalCutError("Native Final Cut dialog must be an object")
        _require_keys(item, {"role", "title"}, "dialog")
        dialogs.append(
            BlockingDialog(
                role=_string(item["role"], "dialog role"),
                title=_string(item["title"], "dialog title"),
            )
        )
    return tuple(dialogs)


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise NativeFinalCutError(f"Native Final Cut {label} has invalid keys")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise NativeFinalCutError(f"Native Final Cut {label} is invalid")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise NativeFinalCutError(f"Native Final Cut {label} is invalid")
    return value
