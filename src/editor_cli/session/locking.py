"""Process-wide mutation lock for one edit session."""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Self


class SessionBusy(RuntimeError):
    """Raised when another process owns the session mutation lock."""


class SessionLock:
    """Hold an advisory exclusive lock on ``<session>/.lock``."""

    def __init__(
        self,
        session_root: Path,
        *,
        blocking: bool = True,
        poll_seconds: float = 0.05,
    ):
        self.session_root = session_root.expanduser().resolve()
        self.blocking = blocking
        self.poll_seconds = poll_seconds
        self.path = self.session_root / ".lock"
        self._fd: int | None = None

    def __enter__(self) -> Self:
        self.session_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(self.path, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if not self.blocking:
                        raise SessionBusy(
                            f"Edit session is locked by another process: "
                            f"{self.session_root.name}"
                        ) from exc
                    time.sleep(self.poll_seconds)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
