import pytest

from editor_cli.session.models import SessionState
from editor_cli.session.store import SessionStore


def test_store_round_trips_state_and_appends_events(tmp_path):
    store = SessionStore(tmp_path)
    store.save_state({"state": SessionState.CAPTURE.value, "pass": 0})
    store.append("capture_started", {"project": "Demo"})
    assert store.load_state()["state"] == "capture"
    assert store.events()[-1]["kind"] == "capture_started"


def test_pending_external_action_survives_restart(tmp_path):
    store = SessionStore(tmp_path)
    token = store.begin_external_action("commandpost.export", {"project": "Demo"})
    reopened = SessionStore(tmp_path)
    assert reopened.pending_actions() == [token]


def test_completed_external_action_is_not_pending(tmp_path):
    store = SessionStore(tmp_path)
    token = store.begin_external_action("commandpost.export", {"project": "Demo"})
    store.complete_external_action(token, {"path": "active-source.fcpxml"})
    assert store.pending_actions() == []


def test_failed_atomic_replace_preserves_previous_state(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    store.save_state({"state": "capture"})

    def fail_replace(*_args):
        raise OSError("simulated crash")

    monkeypatch.setattr("editor_cli.session.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated crash"):
        store.save_state({"state": "preserve"})

    assert store.load_state() == {"state": "capture"}
    assert list(tmp_path.glob("state-*.json")) == []
