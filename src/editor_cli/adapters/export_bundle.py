"""Materialize the XML document returned in a native Final Cut export bundle."""

from pathlib import Path
import os
import shutil
import tempfile


def materialize_export(bundle: Path, destination: Path, session_root: Path) -> None:
    """Preserve the bundle and publish its document without replacing any output."""
    if not all(path.is_absolute() for path in (bundle, destination, session_root)):
        raise ValueError("Export paths must be absolute")
    if destination.suffix != ".fcpxml" or bundle != destination.with_suffix(".fcpxmld"):
        raise ValueError("Export receipt does not match the requested document")
    if session_root.is_symlink():
        raise ValueError("Export session root must not be a symlink")
    for path in (bundle, destination):
        relative = path.relative_to(session_root)
        if not relative.parts or ".." in relative.parts:
            raise ValueError("Export paths must be below the session root")
        current = session_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Export paths must not contain symlinks")
    document = bundle / "Info.fcpxml"
    if not bundle.is_dir() or document.is_symlink() or not document.is_file():
        raise ValueError("Native export bundle has no regular XML document")
    if destination.exists():
        raise FileExistsError(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            with document.open("rb") as source:
                shutil.copyfileobj(source, output)
        # Linking publishes a complete file and fails if a concurrent writer won.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
