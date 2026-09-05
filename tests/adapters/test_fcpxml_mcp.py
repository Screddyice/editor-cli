import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from editor_cli.adapters.fcpxml_mcp import (
    ALLOWED_TOOLS,
    FCPXMLMCPClient,
    FCPXMLMCPError,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeTransport:
    def __init__(self):
        self.initialized = False
        self.params = None
        self.calls = []

    @asynccontextmanager
    async def open(self, params):
        self.params = params
        yield FakeSession(self)


class FakeSession:
    def __init__(self, transport):
        self.transport = transport

    async def initialize(self):
        self.transport.initialized = True

    async def call_tool(self, tool, arguments):
        self.transport.calls.append((tool, arguments))
        payload = json.dumps({"timeline": {"project": "Demo"}})
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text=payload)],
            structuredContent=None,
        )

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name) for name in sorted(ALLOWED_TOOLS)]
        )


@pytest.mark.anyio
async def test_fcpxml_client_initializes_and_calls_grouped_tool(tmp_path):
    transport = FakeTransport()
    client = FCPXMLMCPClient(
        ("uvx", "fcp-mcp-server==0.22.1"),
        journal_root=tmp_path,
        transport=transport,
    )
    result = await client.call(
        "inspect", {"action": "analyze_timeline", "args": {"filepath": "/tmp/a.fcpxml"}}
    )
    assert result["timeline"]["project"] == "Demo"
    assert transport.initialized is True
    assert transport.params.env["FCP_MCP_JOURNAL"] == str(tmp_path.resolve())


@pytest.mark.anyio
async def test_fcpxml_client_rejects_unwrapped_tool():
    client = FCPXMLMCPClient(("uvx", "fcp-mcp-server==0.22.1"))
    with pytest.raises(FCPXMLMCPError, match="unsupported"):
        await client.call("raw_shell", {})


@pytest.mark.anyio
async def test_real_pinned_server_lists_only_grouped_tools(tmp_path):
    client = FCPXMLMCPClient(
        ("uvx", "fcp-mcp-server==0.22.1"),
        journal_root=tmp_path,
        allowed_roots=(tmp_path,),
    )
    assert set(await client.list_tools()) == ALLOWED_TOOLS
