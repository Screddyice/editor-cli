"""Four grouped MCP tools for the Final Cut closed-loop controller."""

from __future__ import annotations

import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from editor_cli.adapters.commandpost import CommandPostClient, CommandPostError

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


LATENITE_APP_IDS = frozenset(
    {
        "com.latenitefilms.ATEMExporter",
        "com.latenitefilms.BRAWToolbox",
        "com.latenitefilms.Capacitor",
        "com.latenitefilms.FastCollections",
        "com.latenitefilms.GyroflowToolbox",
        "com.latenitefilms.LUTRobot",
        "com.latenitefilms.MarkerToolbox",
        "com.latenitefilms.Metaburner",
        "com.latenitefilms.NewsImport",
        "com.latenitefilms.RecallToolbox",
        "com.latenitefilms.TransferToolbox",
        "com.latenitefilms.ScriptStar",
        "com.latenitefilms.NotionToolbox",
        "com.latenitefilms.SmartScriptPro",
        "com.latenitefilms.SyncScriptPro",
        "com.latenitefilms.TimecodeToolbox",
        "com.latenitefilms.VFXToolbox",
        "com.latenitefilms.KeyframeToolbox",
        "com.latenitefilms.OutputToolbox",
        "com.latenitefilms.SmartLevels",
    }
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


def device_report(
    applications: Path = Path("/Applications"),
    *,
    listener_runner=subprocess.run,
    skill_paths: tuple[Path, Path] | None = None,
) -> dict[str, Any]:
    final_cut, info = _find_app(
        applications, "*Final Cut*.app", "com.apple.FinalCutApp"
    )
    reported_final_cut = final_cut or applications / "Final Cut Pro.app"
    commandpost, commandpost_info = _find_app(
        applications, "CommandPost.app", "org.latenitefilms.CommandPost"
    )
    license_app = None
    for candidate in sorted(applications.glob("*.app")):
        info_path = candidate / "Contents/Info.plist"
        if not info_path.is_file():
            continue
        try:
            with info_path.open("rb") as handle:
                candidate_info = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException):
            continue
        if candidate_info.get("CFBundleIdentifier") in LATENITE_APP_IDS:
            license_app = candidate.stem
            break

    bridge: dict[str, Any] = {"available": False, "loopback_only": False}
    try:
        bridge = {
            "available": True,
            **CommandPostClient("ws://127.0.0.1:27480/").doctor(runner=listener_runner),
        }
    except CommandPostError as exc:
        bridge["error"] = str(exc)

    codex_watch, claude_watch = skill_paths or (
        Path("~/.codex/skills/watch/SKILL.md").expanduser(),
        Path("~/.claude/skills/watch/SKILL.md").expanduser(),
    )
    report = {
        "final_cut": {
            "installed": final_cut is not None,
            "version": info.get("CFBundleShortVersionString"),
            "build": info.get("CFBundleVersion"),
            "path": str(reported_final_cut),
        },
        "commandpost": {
            "installed": commandpost is not None,
            "path": str(commandpost or applications / "CommandPost.app"),
            "version": commandpost_info.get("CFBundleShortVersionString"),
            "license_app": license_app,
            "bridge": bridge,
        },
        "watch": {
            "codex": codex_watch.is_file(),
            "claude_code": claude_watch.is_file(),
        },
    }
    report["ready"] = bool(
        report["final_cut"]["installed"]
        and report["commandpost"]["installed"]
        and report["commandpost"]["license_app"]
        and report["commandpost"]["bridge"]["loopback_only"]
        and report["watch"]["codex"]
        and report["watch"]["claude_code"]
    )
    return report


def build_default_services() -> ServiceRegistry:
    from editor_cli.services import build_services

    return build_services(doctor=device_report)


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
