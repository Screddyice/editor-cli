"""Typed, sandboxed access to the pinned FCPXML MCP server."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ALLOWED_TOOLS = frozenset(
    {
        "inspect",
        "diagnose",
        "edit",
        "mark",
        "generate",
        "transcript",
        "deliver",
        "preview",
        "watch",
        "index",
        "scenes",
        "organize",
        "find",
    }
)


class FCPXMLMCPError(RuntimeError):
    """Raised for transport, protocol, and upstream tool failures."""


class MCPTransport(Protocol):
    def open(self, params: StdioServerParameters): ...


class StdioMCPTransport:
    @asynccontextmanager
    async def open(
        self, params: StdioServerParameters
    ) -> AsyncIterator[ClientSession]:
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                yield session


def _text_content(content: list[Any]) -> str:
    return "\n".join(
        block.text for block in content if isinstance(getattr(block, "text", None), str)
    )


class FCPXMLMCPClient:
    def __init__(
        self,
        command: tuple[str, ...],
        *,
        journal_root: Path | None = None,
        allowed_roots: tuple[Path, ...] = (),
        transport: MCPTransport | None = None,
    ):
        if not command:
            raise ValueError("FCPXML MCP command cannot be empty")
        self.command = command
        self.journal_root = journal_root.expanduser().resolve() if journal_root else None
        self.allowed_roots = tuple(root.expanduser().resolve() for root in allowed_roots)
        self.transport = transport or StdioMCPTransport()

    def _parameters(self) -> StdioServerParameters:
        env = dict(os.environ)
        if self.journal_root is not None:
            self.journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            env["FCP_MCP_JOURNAL"] = str(self.journal_root)
        roots = self.allowed_roots
        if not roots and self.journal_root is not None:
            roots = (self.journal_root,)
        if roots:
            env["FCP_PROJECTS_DIRS"] = os.pathsep.join(str(root) for root in roots)
        return StdioServerParameters(
            command=self.command[0],
            args=list(self.command[1:]),
            env=env,
        )

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in ALLOWED_TOOLS:
            raise FCPXMLMCPError(f"unsupported FCPXML tool: {tool}")
        async with self.transport.open(self._parameters()) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments=arguments)
        if result.isError:
            raise FCPXMLMCPError(_text_content(result.content))
        if isinstance(result.structuredContent, dict):
            return result.structuredContent
        text = _text_content(result.content)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    async def list_tools(self) -> tuple[str, ...]:
        async with self.transport.open(self._parameters()) as session:
            await session.initialize()
            result = await session.list_tools()
        return tuple(tool.name for tool in result.tools)
