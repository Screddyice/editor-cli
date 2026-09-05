import pytest

from editor_cli.session.locking import SessionBusy, SessionLock


def test_second_process_cannot_lock_same_session(tmp_path):
    session = tmp_path / "session"
    first = SessionLock(session)

    with (
        first,
        pytest.raises(SessionBusy, match="locked"),
        SessionLock(session, blocking=False),
    ):
        pass


def test_lock_uses_session_dot_lock_file(tmp_path):
    session = tmp_path / "session"

    with SessionLock(session):
        lock_path = session / ".lock"
        assert lock_path.is_file()
        assert lock_path.stat().st_mode & 0o777 == 0o600
