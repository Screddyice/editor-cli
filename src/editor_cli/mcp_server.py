"""Four grouped MCP tools for the Final Cut closed-loop controller."""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
except ImportError:  # pragma: no cover - compatibility with MCP 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

    ArgModelBase = None


class ServiceGroup(Protocol):
    async def dispatch(self, action: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ServiceRegistry:
    session: ServiceGroup
    timeline: ServiceGroup
    media: ServiceGroup
    verify: ServiceGroup


class DeviceSessionService:
    async def dispatch(self, action: str, **_kwargs: Any) -> dict[str, Any]:
        if action != "doctor":
            raise RuntimeError("Run editor-cli setup before starting an edit session")
        return device_report()


class UnconfiguredService:
    def __init__(self, name: str):
        self.name = name

    async def dispatch(self, _action: str, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(
            f"The {self.name} service is unavailable until editor-cli setup completes"
        )


def _find_app(
    applications: Path, pattern: str, bundle_identifier: str
) -> tuple[Path | None, dict[str, Any]]:
    for candidate in sorted(applications.glob(pattern)):
        info_path = candidate / "Contents/Info.plist"
        if not info_path.is_file():
            continue
        try:
            with info_path.open("rb") as handle:
                info = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException):
            continue
        if info.get("CFBundleIdentifier") == bundle_identifier:
            return candidate, info
    return None, {}


def device_report(applications: Path = Path("/Applications")) -> dict[str, Any]:
    final_cut, info = _find_app(
        applications, "*Final Cut*.app", "com.apple.FinalCutApp"
    )
    reported_final_cut = final_cut or applications / "Final Cut Pro.app"
    commandpost = applications / "CommandPost.app"
    codex_watch = Path("~/.codex/skills/watch/SKILL.md").expanduser()
    claude_watch = Path("~/.claude/skills/watch/SKILL.md").expanduser()
    return {
        "final_cut": {
            "installed": final_cut is not None,
            "version": info.get("CFBundleShortVersionString"),
            "build": info.get("CFBundleVersion"),
            "path": str(reported_final_cut),
        },
        "commandpost": {
            "installed": commandpost.is_dir(),
            "path": str(commandpost),
        },
        "watch": {
            "codex": codex_watch.is_file(),
            "claude_code": claude_watch.is_file(),
        },
    }


def build_default_services() -> ServiceRegistry:
    return ServiceRegistry(
        session=DeviceSessionService(),
        timeline=UnconfiguredService("timeline"),
        media=UnconfiguredService("media"),
        verify=UnconfiguredService("verification"),
    )


def create_mcp(services: ServiceRegistry | None = None) -> MCPServer:
    if ArgModelBase is not None:
        # MCP 2.x builds each tool's top-level model from this base. Its default
        # accepts unknown keys, which is unsafe for an application controller.
        ArgModelBase.model_config["extra"] = "forbid"

    registry = services or build_default_services()
    server = MCPServer(
        "editor-cli",
        instructions=(
            "Control the selected Final Cut project through a source-preserving, "
            "rendered-review edit loop. The user performs final export."
        ),
    )

    @server.tool()
    async def editor_session(
        action: Literal["doctor", "start", "status", "resume", "finish"],
        prompt: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect device readiness and manage persisted edit sessions."""
        return await registry.session.dispatch(
            action, prompt=prompt, session_id=session_id
        )

    @server.tool()
    async def editor_timeline(
        action: Literal["inspect", "apply", "diff", "undo"],
        session_id: str,
        edit_program: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inspect or change a session timeline through typed edit operations."""
        return await registry.timeline.dispatch(
            action, session_id=session_id, edit_program=edit_program
        )

    @server.tool()
    async def editor_media(
        action: Literal["acquire", "list"],
        session_id: str,
        url: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """Acquire public HTTPS media or list session-approved assets."""
        return await registry.media.dispatch(
            action, session_id=session_id, url=url, purpose=purpose
        )

    @server.tool()
    async def editor_verify(
        action: Literal["preview", "watch", "record", "compare"],
        session_id: str,
        pass_number: int | None = None,
        report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read rendered evidence and record required-edit checks."""
        return await registry.verify.dispatch(
            action,
            session_id=session_id,
            pass_number=pass_number,
            report=report,
        )

    return server


mcp = create_mcp()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
