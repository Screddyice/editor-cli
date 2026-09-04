from typer.testing import CliRunner

from editor_cli.cli import app
from editor_cli.mcp_server import ServiceRegistry


class FakeGroup:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def dispatch(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return self.result


def fake_registry(session_result):
    session = FakeGroup(session_result)
    unavailable = FakeGroup({"ok": True})
    return ServiceRegistry(
        session=session,
        timeline=unavailable,
        media=unavailable,
        verify=unavailable,
    )


def test_edit_active_captures_request_for_agent_loop(monkeypatch):
    services = fake_registry(
        {
            "session_id": "a" * 32,
            "state": "apply",
            "pass_count": 0,
            "best_candidate": None,
            "failed_checks": [],
        }
    )
    monkeypatch.setattr(
        "editor_cli.mcp_server.build_default_services", lambda: services
    )

    result = CliRunner().invoke(
        app, ["edit-active", "remove gaps and add a reaction at 00:12"]
    )

    assert result.exit_code == 0
    assert "session" in result.output.lower()
    assert "continue in claude code or codex" in result.output.lower()
    assert services.session.calls == [
        (
            "start",
            {
                "prompt": "remove gaps and add a reaction at 00:12",
                "session_id": None,
            },
        )
    ]


def test_session_status_reports_blocked_checks(monkeypatch):
    services = fake_registry(
        {
            "session_id": "b" * 32,
            "state": "blocked",
            "pass_count": 3,
            "best_candidate": {"number": 2},
            "failed_checks": ["meme_insert"],
        }
    )
    monkeypatch.setattr(
        "editor_cli.mcp_server.build_default_services", lambda: services
    )

    result = CliRunner().invoke(app, ["session", "status", "b" * 32])

    assert result.exit_code == 2
    assert "meme_insert" in result.output
    assert services.session.calls == [
        ("status", {"prompt": None, "session_id": "b" * 32})
    ]


def test_session_resume_uses_the_same_persisted_controller(monkeypatch):
    services = fake_registry(
        {
            "session_id": "c" * 32,
            "state": "correct",
            "pass_count": 1,
            "best_candidate": {"number": 1},
            "failed_checks": ["title_visible"],
        }
    )
    monkeypatch.setattr(
        "editor_cli.mcp_server.build_default_services", lambda: services
    )

    result = CliRunner().invoke(app, ["session", "resume", "c" * 32])

    assert result.exit_code == 0
    assert "correct" in result.output
    assert "title_visible" in result.output
    assert services.session.calls == [
        ("resume", {"prompt": None, "session_id": "c" * 32})
    ]
