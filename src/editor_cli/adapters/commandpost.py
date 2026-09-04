"""Restricted client for the loopback CommandPost control bridge."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit

from websockets.asyncio.client import connect


ALLOWED_HANDLERS = frozenset(
    {
        "global_menuactions",
        "global_handler",
        "fcpx_videoEffect",
        "fcpx_audioEffect",
        "fcpx_generator",
        "fcpx_title",
        "fcpx_transition",
    }
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class CommandPostError(RuntimeError):
    """Raised when the CommandPost boundary or command fails."""


def require_loopback_listeners(output: str, port: int) -> tuple[str, ...]:
    """Return listener addresses, failing closed on wildcard or LAN binds."""
    pattern = re.compile(rf"TCP\s+(\S+):{port}\s+\(LISTEN\)")
    addresses = tuple(
        match.group(1).removeprefix("[").removesuffix("]")
        for line in output.splitlines()
        if (match := pattern.search(line))
    )
    if not addresses:
        raise CommandPostError(f"No CommandPost listener found on port {port}")
    if any(address not in LOOPBACK_HOSTS for address in addresses):
        raise CommandPostError("CommandPost listener must bind only to loopback")
    return addresses


class CommandPostClient:
    def __init__(self, url: str, timeout_seconds: float = 20.0):
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in LOOPBACK_HOSTS:
            raise CommandPostError("CommandPost must use an unencrypted loopback WebSocket")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.port = parsed.port or 80

    def command_message(
        self, handler: str, action_id: str, **parameters: Any
    ) -> dict[str, Any]:
        if handler not in ALLOWED_HANDLERS:
            raise CommandPostError(f"Handler is outside the allowlist: {handler}")
        if not action_id:
            raise CommandPostError("CommandPost action ID cannot be empty")
        return {
            "type": "command",
            "id": str(uuid.uuid4()),
            "payload": {
                "handler": handler,
                "actionId": action_id,
                "parameters": parameters,
            },
        }

    async def request(self, message: dict[str, Any]) -> dict[str, Any]:
        try:
            async with connect(self.url, open_timeout=self.timeout_seconds) as socket:
                await socket.send(json.dumps(message))
                raw = await asyncio.wait_for(socket.recv(), self.timeout_seconds)
        except (OSError, TimeoutError) as exc:
            raise CommandPostError(f"CommandPost request failed: {exc}") from exc

        try:
            response = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CommandPostError("CommandPost returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise CommandPostError("CommandPost response must be an object")
        if response.get("id") != message.get("id"):
            raise CommandPostError("CommandPost returned an uncorrelated response")
        if response.get("status") == "error" or response.get("error"):
            raise CommandPostError(str(response.get("error", "unknown error")))
        if response.get("status") != "success":
            raise CommandPostError("CommandPost response did not report success")
        return response

    def doctor(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> dict[str, Any]:
        completed = runner(
            ["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        addresses = require_loopback_listeners(completed.stdout, self.port)
        return {"url": self.url, "listeners": addresses, "loopback_only": True}
