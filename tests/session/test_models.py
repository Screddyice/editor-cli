import pytest

from editor_cli.session.models import (
    EditOperation,
    EditProgram,
    EditRequest,
    SessionState,
)


def test_session_state_has_closed_loop_order():
    assert [state.value for state in SessionState] == [
        "idle",
        "capture",
        "preserve",
        "analyze",
        "apply",
        "import",
        "preview",
        "verify",
        "correct",
        "ready",
        "blocked",
    ]


def test_edit_request_normalizes_required_operations():
    request = EditRequest(prompt="  remove gaps  ", required_operations=("gaps",))
    assert request.prompt == "remove gaps"
    assert request.required_operations == ("gaps",)


def test_edit_program_rejects_unwrapped_action():
    with pytest.raises(ValueError, match="Unsupported edit action"):
        EditProgram((EditOperation("edit", "run_shell", {}),))
