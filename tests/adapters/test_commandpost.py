import json

import pytest
from websockets.asyncio.server import serve

from editor_cli.adapters.commandpost import (
    CommandPostClient,
    CommandPostError,
    require_loopback_listeners,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_client_rejects_unknown_handler():
    client = CommandPostClient("ws://127.0.0.1:27480/")
    with pytest.raises(CommandPostError, match="allowlist"):
        client.command_message("global_applescript", "runAnything")


def test_client_rejects_non_loopback_url():
    with pytest.raises(CommandPostError, match="loopback"):
        CommandPostClient("ws://192.168.1.50:27480/")


def test_menu_command_message_has_request_id():
    client = CommandPostClient("ws://127.0.0.1:27480/")
    message = client.command_message(
        "global_menuactions", "Final Cut Pro/File/Export XML"
    )
    assert message["type"] == "command"
    assert message["payload"]["handler"] == "global_menuactions"
    assert message["id"]


@pytest.mark.anyio
async def test_request_round_trips_a_correlated_response():
    async def handler(socket):
        request = json.loads(await socket.recv())
        await socket.send(
            json.dumps(
                {
                    "type": "response",
                    "id": request["id"],
                    "status": "success",
                    "result": {"ok": True},
                }
            )
        )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = CommandPostClient(f"ws://127.0.0.1:{port}/")
        response = await client.request(
            client.command_message("global_handler", "fcpxUndo")
        )

    assert response["result"] == {"ok": True}


@pytest.mark.anyio
async def test_request_rejects_commandpost_error():
    async def handler(socket):
        request = json.loads(await socket.recv())
        await socket.send(
            json.dumps(
                {
                    "type": "response",
                    "id": request["id"],
                    "status": "error",
                    "error": "action failed",
                }
            )
        )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = CommandPostClient(f"ws://127.0.0.1:{port}/")
        with pytest.raises(CommandPostError, match="action failed"):
            await client.request(client.command_message("global_handler", "fcpxUndo"))


def test_listener_probe_accepts_only_loopback_addresses():
    output = """COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
CommandPo 101 user 10u IPv4 0x0 0t0 TCP 127.0.0.1:27480 (LISTEN)
CommandPo 101 user 11u IPv6 0x0 0t0 TCP [::1]:27480 (LISTEN)
"""
    assert require_loopback_listeners(output, port=27480) == (
        "127.0.0.1",
        "::1",
    )


@pytest.mark.parametrize("address", ["*", "0.0.0.0", "192.168.1.50", "[::]"])
def test_listener_probe_rejects_wildcard_and_lan_addresses(address):
    output = (
        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
        f"CommandPo 101 user 10u IPv4 0x0 0t0 TCP {address}:27480 (LISTEN)\n"
    )
    with pytest.raises(CommandPostError, match="loopback"):
        require_loopback_listeners(output, port=27480)


def test_commandpost_builds_only_named_editor_controller_actions():
    client = CommandPostClient("ws://127.0.0.1:27480/")

    message = client.controller_message(
        "export_xml", destination="/tmp/session/source.fcpxml"
    )

    assert message["payload"] == {
        "handler": "editor_cli",
        "actionId": "export_xml",
        "parameters": {"destination": "/tmp/session/source.fcpxml"},
    }
    with pytest.raises(CommandPostError, match="controller action"):
        client.controller_message("run_shell", command="anything")
