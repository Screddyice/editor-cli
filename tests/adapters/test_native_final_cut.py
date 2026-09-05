import json
import subprocess
from pathlib import Path
from typing import Literal, get_type_hints

import pytest

from editor_cli.adapters.native_final_cut import (
    ExportReceipt,
    NativeFinalCutClient,
    NativeFinalCutError,
    ShareReceipt,
)
from editor_cli.session.models import ProjectIdentity


def identity(name: str = "Demo") -> ProjectIdentity:
    return ProjectIdentity("Canary Library", "Canary Event", name, 12.0)


def identity_json(name: str = "Demo") -> dict[str, object]:
    return {
        "library": "Canary Library",
        "event": "Canary Event",
        "project": name,
        "duration_seconds": 12.0,
    }


def response(result: dict[str, object]) -> str:
    return json.dumps({"ok": True, "result": {"protocolVersion": 1, **result}}) + "\n"


def probe_response(**overrides: object) -> str:
    result: dict[str, object] = {
        "bundleIdentifier": "com.apple.FinalCutApp",
        "version": "12.3",
        "ready": True,
        "accessibilityTrusted": True,
        "automationAuthorized": True,
        "libraryNames": ["Canary Library"],
        "activeProject": identity_json(),
        "dialogs": [],
    }
    result.update(overrides)
    return response(result)


class FakeRunner:
    def __init__(self, stdout: str, *, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args: object, **kwargs: object):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    @property
    def input(self) -> str:
        return self.calls[-1][1]["input"]  # type: ignore[return-value]


class InvalidUTF8Runner:
    def __call__(self, *args: object, **kwargs: object):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


def client(tmp_path: Path, runner: FakeRunner) -> NativeFinalCutClient:
    bridge = tmp_path / "bridge"
    bridge.write_bytes(b"native helper")
    return NativeFinalCutClient(bridge, runner=runner, action_timeout=7)


def test_native_client_sends_one_strict_request(tmp_path):
    runner = FakeRunner(probe_response())
    native = client(tmp_path, runner)

    result = native.probe(tmp_path / "session")

    assert len(runner.calls) == 1
    args, kwargs = runner.calls[0]
    assert args == ([str((tmp_path / "bridge").resolve())],)
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == 7
    assert kwargs["check"] is False
    request = json.loads(runner.input)
    assert set(request) == {"protocolVersion", "action", "sessionRoot", "payload"}
    assert request == {
        "protocolVersion": 1,
        "action": "probe",
        "sessionRoot": str((tmp_path / "session").resolve()),
        "payload": {},
    }
    assert result.active_project == identity()
    assert len(result.helper_sha256) == 64


@pytest.mark.parametrize(
    ("invoke", "action", "payload"),
    [
        (
            lambda native, root: native.duplicate_project(
                identity(), "Demo Copy", root
            ),
            "duplicate_project",
            {
                "expected": identity_json(),
                "name": "Demo Copy",
                "timeout": 7,
            },
        ),
        (
            lambda native, root: native.export_xml(
                identity(), root / "source.fcpxml", root
            ),
            "export_xml",
            {
                "expected": identity_json(),
                "output": None,
                "timeout": 7,
            },
        ),
        (
            lambda native, root: native.import_xml(
                identity(), root / "candidate.fcpxml", root
            ),
            "import_xml",
            {
                "expected": identity_json(),
                "source": None,
                "timeout": 7,
            },
        ),
        (
            lambda native, root: native.open_project(identity(), root),
            "open_project",
            {"expected": identity_json(), "timeout": 7},
        ),
        (
            lambda native, root: native.share_preview(
                identity(), root / "pass.mov", root
            ),
            "share_preview",
            {
                "expected": identity_json(),
                "output": None,
                "timeout": 7,
            },
        ),
        (
            lambda native, root: native.inspect_dialogs(root),
            "inspect_dialogs",
            {},
        ),
    ],
)
def test_native_client_allowlists_typed_actions(tmp_path, invoke, action, payload):
    root = (tmp_path / "session").resolve()
    path = root / (
        "source.fcpxml"
        if action == "export_xml"
        else "candidate.fcpxml"
        if action == "import_xml"
        else "pass.mov"
    )
    result_by_action = {
        "duplicate_project": {"project": identity_json("Demo Copy")},
        "export_xml": {
            "kind": "fcpxml_export",
            "project": identity_json(),
            "output": str(path),
        },
        "import_xml": {"project": identity_json()},
        "open_project": {"project": identity_json()},
        "share_preview": {
            "kind": "final_cut_share",
            "project": identity_json(),
            "output": str(path),
        },
        "inspect_dialogs": {"dialogs": []},
    }
    runner = FakeRunner(response(result_by_action[action]))
    native = client(tmp_path, runner)

    invoke(native, root)

    request = json.loads(runner.input)
    if "output" in payload:
        payload["output"] = str(path)
    if "source" in payload:
        payload["source"] = str(path)
    assert request["action"] == action
    assert request["payload"] == payload


def test_native_client_rejects_unbound_share_result(tmp_path):
    root = tmp_path / "session"
    destination = root / "pass.mov"
    runner = FakeRunner(
        response(
            {
                "kind": "final_cut_share",
                "project": identity_json("Wrong Project"),
                "output": str(destination.resolve()),
            }
        )
    )
    native = client(tmp_path, runner)

    with pytest.raises(NativeFinalCutError, match="identity"):
        native.share_preview(identity(), destination, root)


def test_native_client_rejects_huge_integer_identity_duration(tmp_path):
    huge_identity = identity_json()
    huge_identity["duration_seconds"] = 10**400
    native = client(
        tmp_path,
        FakeRunner(response({"project": huge_identity})),
    )

    with pytest.raises(NativeFinalCutError, match="duration"):
        native.open_project(identity(), tmp_path / "session")


def test_native_client_rejects_nul_in_receipt_path(tmp_path):
    root = tmp_path / "session"
    destination = root / "pass.mov"
    native = client(
        tmp_path,
        FakeRunner(
            response(
                {
                    "kind": "final_cut_share",
                    "project": identity_json(),
                    "output": str(root / "bad\x00.mov"),
                }
            )
        ),
    )

    with pytest.raises(NativeFinalCutError, match="output"):
        native.share_preview(identity(), destination, root)


def test_native_client_rejects_relative_receipt_output(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    destination = root / "pass.mov"
    runner = FakeRunner(
        response(
            {
                "kind": "final_cut_share",
                "project": identity_json(),
                "output": "pass.mov",
            }
        )
    )
    native = client(tmp_path, runner)
    monkeypatch.chdir(root)

    with pytest.raises(NativeFinalCutError, match="output"):
        native.share_preview(identity(), destination, root)


def test_native_client_rejects_paths_outside_session(tmp_path):
    runner = FakeRunner(response({}))
    native = client(tmp_path, runner)

    with pytest.raises(NativeFinalCutError, match="session root"):
        native.export_xml(identity(), tmp_path / "outside.fcpxml", tmp_path / "session")

    assert runner.calls == []


def test_native_client_rejects_symlink_escape(tmp_path):
    root = tmp_path / "session"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    runner = FakeRunner(response({}))
    native = client(tmp_path, runner)

    with pytest.raises(NativeFinalCutError, match="session root"):
        native.import_xml(identity(), root / "escape" / "candidate.fcpxml", root)

    assert runner.calls == []


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ('{"ok":true,"result":', "response"),
        ('{"ok":true,"result":{"protocolVersion":1}}\n{}\n', "response"),
        ('{"ok":true,"result":{"protocolVersion":1},"extra":true}\n', "keys"),
        ('{"ok":true,"result":{"protocolVersion":2}}\n', "protocol"),
    ],
)
def test_native_client_fails_closed_on_malformed_or_extra_response(
    tmp_path, stdout, message
):
    native = client(tmp_path, FakeRunner(stdout))

    with pytest.raises(NativeFinalCutError, match=message):
        native.probe(tmp_path / "session")


def test_native_client_rejects_response_larger_than_one_mibibyte(tmp_path):
    native = client(tmp_path, FakeRunner(" " * 1_048_577))

    with pytest.raises(NativeFinalCutError, match="1 MiB"):
        native.probe(tmp_path / "session")


def test_native_client_wraps_invalid_utf8_response(tmp_path):
    bridge = tmp_path / "bridge"
    bridge.write_bytes(b"native helper")
    native = NativeFinalCutClient(bridge, runner=InvalidUTF8Runner())

    with pytest.raises(NativeFinalCutError, match="text response"):
        native.probe(tmp_path / "session")


def test_native_client_surfaces_strict_helper_error(tmp_path):
    runner = FakeRunner(
        '{"error":"Final Cut displayed a blocking dialog.","ok":false}\n',
        returncode=1,
    )
    native = client(tmp_path, runner)

    with pytest.raises(NativeFinalCutError, match="blocking dialog"):
        native.inspect_dialogs(tmp_path / "session")


def test_native_client_rejects_extra_result_fields(tmp_path):
    native = client(tmp_path, FakeRunner(probe_response(extra="not allowed")))

    with pytest.raises(NativeFinalCutError, match="result.*keys"):
        native.probe(tmp_path / "session")


def test_native_client_rejects_duplicate_response_keys(tmp_path):
    stdout = probe_response().replace('"ok": true', '"ok": true, "ok": true', 1)
    native = client(tmp_path, FakeRunner(stdout))

    with pytest.raises(NativeFinalCutError, match="valid JSON"):
        native.probe(tmp_path / "session")


def test_native_client_wraps_pathological_json_nesting(tmp_path):
    nested = "[" * 10_000 + "]" * 10_000
    native = client(
        tmp_path,
        FakeRunner('{"ok":true,"result":' + nested + "}\n"),
    )

    with pytest.raises(NativeFinalCutError, match="valid JSON"):
        native.probe(tmp_path / "session")


def test_native_client_decodes_sanitized_dialogs(tmp_path):
    runner = FakeRunner(
        response(
            {
                "dialogs": [
                    {"role": "AXSheet", "title": "Missing Media"},
                    {"role": "AXDialog", "title": "Relink Files"},
                ]
            }
        )
    )
    native = client(tmp_path, runner)

    dialogs = native.inspect_dialogs(tmp_path / "session")

    assert [(item.role, item.title) for item in dialogs] == [
        ("AXSheet", "Missing Media"),
        ("AXDialog", "Relink Files"),
    ]


def test_native_client_accepts_empty_dialog_title(tmp_path):
    native = client(
        tmp_path,
        FakeRunner(response({"dialogs": [{"role": "AXSheet", "title": ""}]})),
    )

    dialogs = native.inspect_dialogs(tmp_path / "session")

    assert [(item.role, item.title) for item in dialogs] == [("AXSheet", "")]


def test_receipt_kind_annotations_match_runtime_contract():
    assert get_type_hints(ExportReceipt)["kind"] == Literal["fcpxml_export"]
    assert get_type_hints(ShareReceipt)["kind"] == Literal["final_cut_share"]
