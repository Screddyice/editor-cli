import json
from pathlib import Path

import pytest

from editor_cli.session.paths import AccessDenied, SessionPaths


def test_allowlist_accepts_session_files_and_exact_media_reference(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    referenced = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([referenced])
    assert paths.require_read(paths.assets / "meme.mp4").is_relative_to(paths.root)
    assert paths.require_read(referenced) == referenced


def test_media_reference_provenance_round_trips_across_restart(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    referenced = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([referenced])
    persisted = json.dumps(
        {"media_references": [str(path) for path in paths.media_references]}
    )

    restored = json.loads(persisted)
    reopened = SessionPaths.create(
        tmp_path / "sessions",
        "abc123",
        media_references=tuple(Path(path) for path in restored["media_references"]),
    )

    assert reopened.media_references == (referenced,)
    assert reopened.require_read(referenced) == referenced


def test_nested_media_path_must_be_exact_reference(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    allowed = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([allowed])

    assert paths.require_read(allowed) == allowed
    with pytest.raises(AccessDenied):
        paths.require_read(allowed.parent / "neighbor.mov")


def test_allowlist_rejects_symlink_escape(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    outside = tmp_path / "private.mov"
    outside.write_bytes(b"private")
    link = paths.assets / "linked.mov"
    link.symlink_to(outside)

    with pytest.raises(AccessDenied):
        paths.require_read(link)
