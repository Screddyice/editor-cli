"""Local reference passthrough."""

from __future__ import annotations

from pathlib import Path


def resolve(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Reference file not found: {path}")
    return str(p)
