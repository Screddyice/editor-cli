import subprocess
import sys
import textwrap

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


def test_subprocess_cannot_lock_session_held_by_parent(tmp_path):
    session = tmp_path / "session"
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from editor_cli.session.locking import SessionBusy, SessionLock

        try:
            with SessionLock(Path(sys.argv[1]), blocking=False):
                pass
        except SessionBusy:
            raise SystemExit(23)
        raise SystemExit(0)
        """
    )

    with SessionLock(session):
        result = subprocess.run(
            [sys.executable, "-c", script, str(session)],
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 23, result.stderr
