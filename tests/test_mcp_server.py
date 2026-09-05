import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

import editor_cli.mcp_server as mcp_server_module
from editor_cli.adapters.native_final_cut import BlockingDialog, NativeProbe
from editor_cli.config import ControllerConfig
from editor_cli.mcp_server import ServiceRegistry, create_mcp, device_report


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeGroup:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def dispatch(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return self.result


def fake_services():
    session = FakeGroup({"final_cut": {"version": "12.3"}})
    return ServiceRegistry(
        session=session,
        timeline=FakeGroup({"ok": True}),
        media=FakeGroup({"ok": True}),
        verify=FakeGroup({"ok": True}),
    )


@pytest.mark.anyio
async def test_mcp_exposes_only_grouped_tools():
    mcp = create_mcp(fake_services())
    names = {tool.name for tool in await mcp.list_tools()}
    assert names == {
        "editor_session",
        "editor_timeline",
        "editor_media",
        "editor_verify",
    }
    assert all(
        tool.input_schema.get("additionalProperties") is False
        for tool in await mcp.list_tools()
    )


@pytest.mark.anyio
async def test_editor_session_doctor_is_read_only():
    services = fake_services()
    mcp = create_mcp(services)
    result = await mcp.call_tool("editor_session", {"action": "doctor"})
    assert result.structured_content["final_cut"]["version"] == "12.3"
    assert services.session.calls == [("doctor", {"prompt": None, "session_id": None})]


@pytest.mark.anyio
async def test_grouped_tools_reject_unknown_keys():
    mcp = create_mcp(fake_services())
    with pytest.raises(ToolError, match="Extra inputs"):
        await mcp.call_tool(
            "editor_session", {"action": "doctor", "run_shell": "anything"}
        )


@pytest.mark.anyio
async def test_stdio_server_initializes_and_lists_four_tools():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "editor_cli.mcp_server"],
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert names == {
        "editor_session",
        "editor_timeline",
        "editor_media",
        "editor_verify",
    }


def native_config(tmp_path: Path) -> tuple[ControllerConfig, str]:
    helper = tmp_path / "bin" / "editor-fcp-bridge"
    helper.parent.mkdir()
    helper.write_bytes(b"installed native helper")
    digest = hashlib.sha256(helper.read_bytes()).hexdigest()
    helper.with_suffix(".json").write_text(
        json.dumps(
            {
                "managed_by": "editor-cli.native-final-cut-helper",
                "metadata_version": 1,
                "protocol_version": 1,
                "sha256": digest,
                "source_sha256": "b" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return (
        ControllerConfig(
            session_root=tmp_path / "sessions",
            native_helper=helper,
            native_protocol_version=1,
        ),
        digest,
    )


def valid_probe(digest: str) -> NativeProbe:
    return NativeProbe(
        protocol_version=1,
        helper_sha256=digest,
        final_cut_bundle_id="com.apple.FinalCutApp",
        final_cut_version="12.3",
        accessibility=True,
        automation=True,
        ready=True,
        dialogs=(),
    )


def test_device_report_requires_native_helper_and_no_paid_app(tmp_path):
    config, digest = native_config(tmp_path)
    probe = Mock(return_value=valid_probe(digest))

    report = device_report(config=config, probe=probe)

    assert report["ready"] is True
    assert report["native_helper"]["installed"] is True
    assert report["native_helper"]["metadata_valid"] is True
    assert report["native_helper"]["sha256"] == digest
    assert report["final_cut"] == {
        "bundle_id": "com.apple.FinalCutApp",
        "version": "12.3",
        "compatible": True,
    }
    assert report["permissions"] == {"accessibility": True, "automation": True}
    assert report["dialogs"] == []
    assert "commandpost" not in report
    assert "license_app" not in json.dumps(report).lower()
    probe.assert_called_once_with(config.session_root)


@pytest.mark.parametrize(
    ("change", "section"),
    [
        ({"protocol_version": 2}, "native_helper"),
        ({"helper_sha256": "c" * 64}, "native_helper"),
        ({"final_cut_version": "12.4"}, "final_cut"),
        ({"final_cut_bundle_id": "example.fake"}, "final_cut"),
        ({"accessibility": False, "ready": False}, "permissions"),
        ({"automation": False, "ready": False}, "permissions"),
        ({"dialogs": (BlockingDialog("AXSheet", "Missing Media"),)}, "dialogs"),
    ],
)
def test_device_report_rejects_incompatible_native_state(tmp_path, change, section):
    config, digest = native_config(tmp_path)
    probe = Mock(return_value=replace(valid_probe(digest), **change))

    report = device_report(config=config, probe=probe)

    assert report["ready"] is False
    assert section in report


@pytest.mark.parametrize("metadata", [None, "not json", "{}"])
def test_device_report_rejects_missing_or_invalid_helper_metadata(tmp_path, metadata):
    config, digest = native_config(tmp_path)
    metadata_path = config.native_helper.with_suffix(".json")
    if metadata is None:
        metadata_path.unlink()
    else:
        metadata_path.write_text(metadata, encoding="utf-8")
    probe = Mock(return_value=valid_probe(digest))

    report = device_report(config=config, probe=probe)

    assert report["ready"] is False
    assert report["native_helper"]["metadata_valid"] is False
    probe.assert_not_called()


@pytest.mark.anyio
async def test_default_mcp_constructs_services_lazily_for_doctor(monkeypatch):
    def fail_build():
        raise FileNotFoundError("watch.py not found")

    monkeypatch.setattr(mcp_server_module, "build_default_services", fail_build)
    monkeypatch.setattr(
        mcp_server_module,
        "device_report",
        lambda: {"ready": False, "native_helper": {"installed": False}},
    )
    server = create_mcp()

    result = await server.call_tool("editor_session", {"action": "doctor"})

    assert result.structured_content["ready"] is False
    assert result.structured_content["native_helper"]["installed"] is False


@pytest.mark.anyio
async def test_default_mcp_builds_services_once_on_first_control_call(monkeypatch):
    services = fake_services()
    build = Mock(return_value=services)
    monkeypatch.setattr(mcp_server_module, "build_default_services", build)
    server = create_mcp()

    first = await server.call_tool(
        "editor_timeline", {"action": "inspect", "session_id": "session-1"}
    )
    second = await server.call_tool(
        "editor_timeline", {"action": "diff", "session_id": "session-1"}
    )

    assert first.structured_content == {"ok": True}
    assert second.structured_content == {"ok": True}
    build.assert_called_once_with()


def test_mcp_module_help_exits_without_starting_stdio():
    result = subprocess.run(
        [sys.executable, "-m", "editor_cli.mcp_server", "--help"],
        capture_output=True,
        check=False,
        input="",
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert "Usage: python -m editor_cli.mcp_server" in result.stdout
