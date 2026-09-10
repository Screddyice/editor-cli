"""Install the direct editing skill without touching Final Cut configuration."""

import importlib.resources
import os
import shlex
import sys
import uuid
from pathlib import Path


MARKER = "<!-- managed by editor-cli.direct -->"


def install_skills(home: Path | None = None) -> dict:
    home = home or Path.home()
    template = (
        importlib.resources.files("editor_cli.resources")
        .joinpath("skills/direct-video-editor/SKILL.md")
        .read_text()
    )
    content = template.replace("@PYTHON@", shlex.quote(sys.executable))
    targets = [
        home / host / "skills" / "direct-video-editor" / "SKILL.md"
        for host in (".codex", ".claude")
    ]
    # Validate both destinations before making changes. Existing managed copies
    # receive a backup; unrelated skills and symlink destinations are untouched.
    for target in targets:
        if any(p.is_symlink() for p in (target, target.parent)):
            raise ValueError(f"Skill destination is a symlink: {target}")
        if target.exists() and MARKER not in target.read_text():
            raise ValueError(f"An unmanaged skill exists at {target}")
    changed = []
    for target in targets:
        if target.exists() and target.read_text() == content:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_name(f"SKILL.md.{uuid.uuid4().hex}.bak")
            backup.write_bytes(target.read_bytes())
        temp = target.with_name(f".SKILL-{uuid.uuid4().hex}.tmp")
        temp.write_text(content)
        os.replace(temp, target)
        changed.append(str(target))
    return {
        "installed": [str(t) for t in targets],
        "changed": changed,
        "python": sys.executable,
        "note": "Use the CLI now; restart agent sessions to refresh discovered skills/MCP tools.",
    }
