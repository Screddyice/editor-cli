"""Idempotent setup primitives for the local Final Cut controller."""

from __future__ import annotations


WATCH_RELEASE = "v0.2.0"


def watch_install_command() -> tuple[str, ...]:
    """Return the pinned shared-skill install command for both agent hosts."""
    return (
        "npx",
        "skills",
        "add",
        f"https://github.com/bradautomates/claude-video/tree/{WATCH_RELEASE}",
        "-g",
        "--agent",
        "claude-code",
        "codex",
        "--skill",
        "watch",
        "-y",
    )
