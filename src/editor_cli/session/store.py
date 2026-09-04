"""Crash-safe snapshots and an append-only edit-session journal."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "journal.jsonl"

    def save_state(self, value: dict[str, Any]) -> None:
        fd, raw = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.root)
        temp = Path(raw)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.state_path)
        finally:
            temp.unlink(missing_ok=True)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        with self.state_path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("Session state must be a JSON object")
        return value

    def append(self, kind: str, data: dict[str, Any]) -> str:
        event_id = str(uuid.uuid4())
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

    def begin_external_action(self, action: str, data: dict[str, Any]) -> str:
        return self.append(
            "external_action_pending",
            {"action": action, "arguments": data},
        )

    def complete_external_action(
        self, token: str, result: dict[str, Any] | None = None
    ) -> str:
        return self.append(
            "external_action_completed",
            {"token": token, "result": result or {}},
        )

    def pending_actions(self) -> list[str]:
        pending: list[str] = []
        completed: set[str] = set()
        for event in self.events():
            if event["kind"] == "external_action_pending":
                pending.append(event["id"])
            elif event["kind"] == "external_action_completed":
                completed.add(event["data"]["token"])
        return [token for token in pending if token not in completed]
