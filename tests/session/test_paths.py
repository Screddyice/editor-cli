from pathlib import Path

import pytest

from editor_cli.session.paths import AccessDenied, SessionPaths


def test_allowlist_accepts_session_files_and_exact_media_reference(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    referenced = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([referenced])
    assert paths.require_read(paths.assets / "meme.mp4").is_relative_to(paths.root)
    assert paths.require_read(referenced) == referenced


def test_allowlist_rejects_reference_sibling(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    referenced = (tmp_path / "source" / "clip.mov").resolve()
    paths.add_media_references([referenced])
    with pytest.raises(AccessDenied):
        paths.require_read(referenced.parent / "private.mov")


def test_allowlist_rejects_symlink_escape(tmp_path: Path):
    paths = SessionPaths.create(tmp_path / "sessions", "abc123")
    outside = tmp_path / "private.mov"
    outside.write_bytes(b"private")
    link = paths.assets / "linked.mov"
    link.symlink_to(outside)

    with pytest.raises(AccessDenied):
        paths.require_read(link)
