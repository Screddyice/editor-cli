"""Packaged source artifacts for the native Final Cut controller."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable


def native_source() -> Traversable:
    """Return the packaged Swift source tree."""
    return files(__package__).joinpath("native")


def final_cut_skill() -> Traversable:
    """Return the packaged Final Cut editor skill tree."""
    return files(__package__).joinpath("skills/final-cut-editor")


def live_canary() -> Traversable:
    """Return the packaged live-canary script."""
    return files(__package__).joinpath("canary/fcp_live_canary.py")


__all__ = ["final_cut_skill", "live_canary", "native_source"]
