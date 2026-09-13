"""Filesystem boundaries for one source-preserving edit session."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


class AccessDenied(PermissionError):
    """Raised when a controller tries to read outside its approved paths."""


@dataclass
class SessionPaths:
    root: Path
    source: Path
    assets: Path
    candidates: Path
    previews: Path
    evidence: Path
    _media_refs: set[Path] = field(default_factory=set)

    @classmethod
    def create(
        cls,
        base: Path,
        session_id: str,
        *,
        media_references: Iterable[Path] = (),
    ) -> SessionPaths:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{6,64}", session_id):
            raise ValueError("Invalid session id")

        root = (base / session_id).expanduser().resolve()
        parts = [
            root / name
            for name in ("source", "assets", "candidates", "previews", "evidence")
        ]
        for path in (root, *parts):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        result = cls(root, *parts)
        result.add_media_references(media_references)
        return result

    def add_media_references(self, paths: Iterable[Path]) -> None:
        self._media_refs.update(path.expanduser().resolve() for path in paths)

    @property
    def media_references(self) -> tuple[Path, ...]:
        return tuple(sorted(self._media_refs))

    def require_read(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if (
            resolved == self.root
            or resolved.is_relative_to(self.root)
            or resolved in self._media_refs
        ):
            return resolved
        raise AccessDenied(f"Path is outside this edit session: {resolved}")
