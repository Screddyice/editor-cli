import plistlib
import subprocess
import sys
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

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


def test_device_report_discovers_creator_studio_final_cut_bundle(tmp_path):
    app = tmp_path / "Final Cut Pro Creator Studio.app"
    info = app / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    with info.open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "com.apple.FinalCutApp",
                "CFBundleShortVersionString": "12.3",
                "CFBundleVersion": "450152",
            },
            handle,
        )

    report = device_report(applications=tmp_path)

    assert report["final_cut"]["installed"] is True
    assert report["final_cut"]["version"] == "12.3"
    assert report["final_cut"]["path"] == str(app)


def test_device_report_requires_latenite_license_app_and_loopback_bridge(tmp_path):
    def write_app(name, bundle, version="1.0", build="1"):
        app = tmp_path / f"{name}.app"
        info = app / "Contents" / "Info.plist"
        info.parent.mkdir(parents=True)
        with info.open("wb") as handle:
            plistlib.dump(
                {
                    "CFBundleIdentifier": bundle,
                    "CFBundleShortVersionString": version,
                    "CFBundleVersion": build,
                },
                handle,
            )
        return app

    write_app("Final Cut Pro Creator Studio", "com.apple.FinalCutApp", "12.3", "450152")
    write_app("CommandPost", "org.latenitefilms.CommandPost", "2.1.0")
    write_app("Fast Collections", "com.latenitefilms.FastCollections", "1.0")

    report = device_report(
        applications=tmp_path,
        listener_runner=lambda *_args, **_kwargs: SimpleNamespace(
            stdout="CommandPo 1 user 1u IPv4 0x0 0t0 TCP 127.0.0.1:27480 (LISTEN)\n"
        ),
        skill_paths=(tmp_path / "watch-a.md", tmp_path / "watch-b.md"),
    )

    assert report["commandpost"]["version"] == "2.1.0"
    assert report["commandpost"]["license_app"] == "Fast Collections"
    assert report["commandpost"]["bridge"]["loopback_only"] is True
    assert report["ready"] is False


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
