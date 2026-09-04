"""Crash-safe snapshots and an append-only edit-session journal."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from editor_cli.session.models import ExternalAction


class SessionBusy(RuntimeError):
    """Raised when another process owns a session mutation lock."""


class StaleSessionState(RuntimeError):
    """Raised when a stale session snapshot loses compare-and-swap."""


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "journal.jsonl"
        self.lock_path = self.root / ".session.lock"

    @contextmanager
    def lock(self, timeout_seconds: float = 5.0) -> Iterator[None]:
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+") as handle:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise SessionBusy(
                            f"Edit session is locked by another process: {self.root.name}"
                        ) from exc
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def save_state(
        self,
        value: dict[str, Any],
        *,
        expected_version: int | None = None,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        context = nullcontext() if lock_held else self.lock()
        with context:
            current = self.load_state()
            current_version = int(current.get("version", 0))
            if expected_version is not None and current_version != expected_version:
                raise StaleSessionState(
                    "Edit session state changed before this update could be saved"
                )
            snapshot = dict(value)
            snapshot["version"] = current_version + 1
            self._atomic_state_write(snapshot)
            value["version"] = snapshot["version"]
            return snapshot

    def _atomic_state_write(self, value: dict[str, Any]) -> None:
        fd, raw = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.root)
        temp = Path(raw)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.state_path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        with self.state_path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise TypeError("Session state must be a JSON object")
        return value

    def compare_and_swap(self, expected_version: int, value: dict[str, Any]) -> None:
        with self.lock():
            current = self.load_state()
            if int(current.get("version", 0)) != expected_version:
                raise StaleSessionState("Session state changed in another process")
            next_value = dict(value)
            next_value["version"] = expected_version + 1
            self.save_state(
                next_value,
                expected_version=expected_version,
                lock_held=True,
            )

    def append(
        self, kind: str, data: dict[str, Any], *, event_id: str | None = None
    ) -> str:
        event_id = event_id or str(uuid.uuid4())
        row = {
            "id": event_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "data": data,
        }
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event_id

    def events(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.journal_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def begin_external_action(
        self,
        action: str,
        data: dict[str, Any],
        *,
        expected: dict[str, Any] | None = None,
        expected_identity: dict[str, Any] | None = None,
        idempotency: dict[str, Any] | None = None,
    ) -> str:
        if expected is not None and (
            expected_identity is not None or idempotency is not None
        ):
            raise ValueError("Pass either expected or legacy action expectations")
        if expected is None:
            expected = {}
            if expected_identity is not None:
                expected["identity"] = dict(expected_identity)
            if idempotency is not None:
                expected["idempotency"] = dict(idempotency)
        token = str(uuid.uuid4())
        record = ExternalAction(
            token=token,
            action=action,
            arguments=dict(data),
            expected=dict(expected),
            status="pending",
        )
        return self.append(
            "external_action",
            asdict(record),
            event_id=token,
        )

    def complete_external_action(
        self, token: str, result: dict[str, Any] | None = None
    ) -> str:
        pending = next(
            (action for action in self.pending_actions() if action.token == token),
            None,
        )
        if pending is None:
            raise ValueError(f"No pending external action: {token}")
        record = ExternalAction(
            token=pending.token,
            action=pending.action,
            arguments=dict(pending.arguments),
            expected=dict(pending.expected),
            status="complete",
        )
        data = asdict(record)
        data["result"] = dict(result or {})
        return self.append(
            "external_action",
            data,
        )

    def pending_actions(self) -> list[ExternalAction]:
        latest: dict[str, ExternalAction] = {}
        for event in self.events():
            if event["kind"] == "external_action":
                data = event["data"]
                record = ExternalAction(
                    token=data["token"],
                    action=data["action"],
                    arguments=dict(data.get("arguments") or {}),
                    expected=dict(data.get("expected") or {}),
                    status=data["status"],
                )
                latest[record.token] = record
        return [action for action in latest.values() if action.status == "pending"]

    def completed_action_result(self, token: str) -> dict[str, Any] | None:
        for event in reversed(self.events()):
            if (
                event["kind"] == "external_action"
                and event["data"].get("status") == "complete"
                and event["data"].get("token") == token
            ):
                return dict(event["data"].get("result") or {})
        return None
