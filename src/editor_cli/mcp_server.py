"""Four grouped MCP tools for the Final Cut closed-loop controller."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from editor_cli.adapters.native_final_cut import (
    NativeFinalCutClient,
    NativeFinalCutError,
    NativeProbe,
)
from editor_cli.config import ControllerConfig, load_controller_config

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


_NATIVE_METADATA_KEYS = frozenset(
    {
        "managed_by",
        "metadata_version",
        "protocol_version",
        "sha256",
        "source_sha256",
    }
)
_NATIVE_MANAGED_BY = "editor-cli.native-final-cut-helper"
_FINAL_CUT_BUNDLE_ID = "com.apple.FinalCutApp"
_FINAL_CUT_VERSION = "12.3"


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _native_metadata(
    config: ControllerConfig,
) -> tuple[dict[str, Any] | None, str | None]:
    helper = config.native_helper.expanduser().resolve()
    metadata_path = helper.with_suffix(".json")
    if (
        not helper.is_file()
        or helper.is_symlink()
        or not metadata_path.is_file()
        or metadata_path.is_symlink()
    ):
        return None, None
    try:
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
        helper_sha256 = _sha256(helper)
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None, None
    if (
        not isinstance(value, dict)
        or set(value) != _NATIVE_METADATA_KEYS
        or value.get("managed_by") != _NATIVE_MANAGED_BY
        or type(value.get("metadata_version")) is not int
        or value.get("metadata_version") != 1
        or type(value.get("protocol_version")) is not int
        or value.get("protocol_version") != config.native_protocol_version
        or not _is_sha256(value.get("sha256"))
        or not _is_sha256(value.get("source_sha256"))
        or value.get("sha256") != helper_sha256
    ):
        return None, helper_sha256
    return value, helper_sha256


def device_report(
    config: ControllerConfig | None = None,
    *,
    probe: Callable[[Path], NativeProbe] | None = None,
) -> dict[str, Any]:
    config = config or load_controller_config()
    helper = config.native_helper.expanduser().resolve()
    metadata, installed_sha256 = _native_metadata(config)
    report: dict[str, Any] = {
        "native_helper": {
            "path": str(helper),
            "installed": helper.is_file() and not helper.is_symlink(),
            "metadata_path": str(helper.with_suffix(".json")),
            "metadata_valid": metadata is not None,
            "sha256": installed_sha256,
            "protocol_version": (
                metadata.get("protocol_version") if metadata is not None else None
            ),
            "compatible": False,
        },
        "final_cut": {"bundle_id": None, "version": None, "compatible": False},
        "permissions": {"accessibility": False, "automation": False},
        "dialogs": [],
        "ready": False,
    }
    if metadata is None:
        report["error"] = "Native Final Cut helper metadata is missing or invalid"
        return report

    probe_call = (
        probe
        or NativeFinalCutClient(
            helper, action_timeout=config.native_action_timeout_seconds
        ).probe
    )
    try:
        state = probe_call(config.session_root)
    except (NativeFinalCutError, OSError, RuntimeError, ValueError) as exc:
        report["error"] = str(exc)
        return report

    helper_compatible = bool(
        state.protocol_version == config.native_protocol_version
        and state.helper_sha256 == metadata["sha256"]
        and state.helper_sha256 == installed_sha256
    )
    final_cut_compatible = bool(
        state.final_cut_bundle_id == _FINAL_CUT_BUNDLE_ID
        and state.final_cut_version == _FINAL_CUT_VERSION
    )
    dialogs = [{"role": dialog.role, "title": dialog.title} for dialog in state.dialogs]
    report["native_helper"].update(
        {
            "sha256": state.helper_sha256,
            "protocol_version": state.protocol_version,
            "compatible": helper_compatible,
        }
    )
    report["final_cut"] = {
        "bundle_id": state.final_cut_bundle_id,
        "version": state.final_cut_version,
        "compatible": final_cut_compatible,
    }
    report["permissions"] = {
        "accessibility": state.accessibility,
        "automation": state.automation,
    }
    report["dialogs"] = dialogs
    report["ready"] = bool(
        helper_compatible
        and final_cut_compatible
        and state.accessibility
        and state.automation
        and state.ready
        and not dialogs
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

    registry = services

    def service_registry() -> ServiceRegistry:
        nonlocal registry
        if registry is None:
            registry = build_default_services()
        return registry

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
        if action == "doctor" and services is None:
            return device_report()
        return await service_registry().session.dispatch(
            action, prompt=prompt, session_id=session_id
        )

    @server.tool()
    async def editor_timeline(
        action: Literal["inspect", "apply", "diff", "undo"],
        session_id: str,
        edit_program: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inspect or change a session timeline through typed edit operations."""
        return await service_registry().timeline.dispatch(
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
        return await service_registry().media.dispatch(
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
        return await service_registry().verify.dispatch(
            action,
            session_id=session_id,
            pass_number=pass_number,
            report=report,
        )

    return server


mcp = create_mcp()


def main() -> None:
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "Usage: python -m editor_cli.mcp_server\n\n"
            "Start the Editor CLI MCP server over standard input and output."
        )
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
