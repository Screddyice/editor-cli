"""Idempotent setup for the local Final Cut controller."""

from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


COMMANDPOST_VERSION = "2.1.0"
COMMANDPOST_DMG_URL = (
    "https://github.com/CommandPost/CommandPost/releases/download/2.1.0/"
    "CommandPost_2.1.0.dmg"
)
COMMANDPOST_DMG_SHA256 = (
    "b1a3ca256053a083b59dd1d1db59b68d9b2ea8b83dc2e5214d0eba921eba5e64"
)
WATCH_RELEASE = "v0.2.0"
CODEX_BLOCK_START = "# BEGIN editor-cli managed MCP"
CODEX_BLOCK_END = "# END editor-cli managed MCP"


class SetupError(RuntimeError):
    """Raised when setup cannot preserve an existing host configuration."""


@dataclass(frozen=True)
class SetupPaths:
    repo_root: Path
    codex_config: Path
    claude_config: Path
    codex_skills: Path
    claude_skills: Path
    commandpost_plugins: Path
    applications: Path = Path("/Applications")

    @classmethod
    def defaults(cls, repo_root: Path | None = None) -> "SetupPaths":
        root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        return cls(
            repo_root=root,
            codex_config=Path("~/.codex/config.toml").expanduser(),
            claude_config=Path("~/.claude.json").expanduser(),
            codex_skills=Path("~/.codex/skills").expanduser(),
            claude_skills=Path("~/.claude/skills").expanduser(),
            commandpost_plugins=Path(
                "~/Library/Application Support/CommandPost/Plugins"
            ).expanduser(),
            applications=Path("/Applications"),
        )


@dataclass
class SetupResult:
    changed: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


class SetupPlatform(Protocol):
    def commandpost_version(self, applications: Path) -> str | None: ...

    def install_commandpost(self, applications: Path) -> None: ...

    def install_watch(self, paths: SetupPaths) -> None: ...

    def verify_mcp(self, python: Path, repo_root: Path) -> bool: ...


def watch_install_command() -> tuple[str, ...]:
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


def backup_before_write(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".editor-cli.bak")
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    return backup


def _watch_version(path: Path) -> str | None:
    skill = path / "watch" / "SKILL.md"
    if not skill.is_file():
        return None
    for line in skill.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.partition(":")[2].strip().strip('"\'')
    return None


def _ensure_symlink(link: Path, target: Path, result: SetupResult, dry_run: bool) -> None:
    target = target.resolve()
    if link.is_symlink() and link.resolve() == target:
        return
    if link.exists() or link.is_symlink():
        raise SetupError(f"Refusing to replace existing path: {link}")
    message = f"link {link} -> {target}"
    result.planned.append(message)
    if dry_run:
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    result.changed.append(message)


def _codex_block(python: Path, repo_root: Path) -> str:
    return "\n".join(
        (
            CODEX_BLOCK_START,
            '[mcp_servers."editor-cli"]',
            f"command = {json.dumps(str(python))}",
            'args = ["-m", "editor_cli.mcp_server"]',
            f"cwd = {json.dumps(str(repo_root))}",
            CODEX_BLOCK_END,
        )
    )


def _merge_codex_config(path: Path, python: Path, repo_root: Path) -> str | None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = _codex_block(python, repo_root)
    if CODEX_BLOCK_START in existing:
        before, remainder = existing.split(CODEX_BLOCK_START, 1)
        if CODEX_BLOCK_END not in remainder:
            raise SetupError("Codex editor-cli MCP block is missing its end marker")
        _, after = remainder.split(CODEX_BLOCK_END, 1)
        prefix = before.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + block + after
    else:
        try:
            parsed = tomllib.loads(existing) if existing.strip() else {}
        except tomllib.TOMLDecodeError as exc:
            raise SetupError(f"Codex config is invalid TOML: {path}") from exc
        if "editor-cli" in parsed.get("mcp_servers", {}):
            raise SetupError(
                "Codex already has an unmanaged editor-cli MCP entry; remove or rename it"
            )
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
    tomllib.loads(updated)
    return None if updated == existing else updated


def _merge_claude_config(path: Path, python: Path, repo_root: Path) -> str | None:
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SetupError(f"Claude config is invalid JSON: {path}") from exc
    else:
        value = {}
    if not isinstance(value, dict):
        raise SetupError("Claude config must contain a JSON object")
    servers = value.setdefault("mcpServers", {})
    desired = {
        "type": "stdio",
        "command": str(python),
        "args": ["-m", "editor_cli.mcp_server"],
        "cwd": str(repo_root),
    }
    if servers.get("editor-cli") == desired:
        return None
    servers["editor-cli"] = desired
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _write_config(
    path: Path, content: str | None, result: SetupResult, dry_run: bool
) -> None:
    if content is None:
        return
    result.planned.append(f"configure {path}")
    if dry_run:
        return
    backup = backup_before_write(path)
    if backup is not None:
        result.backups.append(backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    result.changed.append(f"configure {path}")


class LocalPlatform:
    def commandpost_version(self, applications: Path) -> str | None:
        info = applications / "CommandPost.app" / "Contents" / "Info.plist"
        if not info.is_file():
            return None
        with info.open("rb") as handle:
            return plistlib.load(handle).get("CFBundleShortVersionString")

    def install_commandpost(self, applications: Path) -> None:
        applications.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="editor-cli-commandpost-") as raw:
            temp = Path(raw)
            dmg = temp / "CommandPost_2.1.0.dmg"
            with urllib.request.urlopen(COMMANDPOST_DMG_URL, timeout=120) as response:
                with dmg.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
            digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
            if digest != COMMANDPOST_DMG_SHA256:
                raise SetupError("CommandPost DMG checksum mismatch")
            mount = temp / "mount"
            mount.mkdir()
            attached = False
            try:
                subprocess.run(
                    [
                        "hdiutil",
                        "attach",
                        str(dmg),
                        "-nobrowse",
                        "-readonly",
                        "-mountpoint",
                        str(mount),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                attached = True
                apps = list(mount.glob("CommandPost*.app"))
                if len(apps) != 1:
                    raise SetupError("CommandPost DMG did not contain one application")
                subprocess.run(
                    ["ditto", str(apps[0]), str(applications / "CommandPost.app")],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            finally:
                if attached:
                    subprocess.run(
                        ["hdiutil", "detach", str(mount)],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
        app = applications / "CommandPost.app"
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app)],
            check=True,
            capture_output=True,
            text=True,
        )

    def install_watch(self, _paths: SetupPaths) -> None:
        subprocess.run(watch_install_command(), check=True)

    def verify_mcp(self, python: Path, repo_root: Path) -> bool:
        code = (
            "import asyncio; from editor_cli.mcp_server import mcp; "
            "assert asyncio.run(mcp.list_tools())"
        )
        completed = subprocess.run(
            [str(python), "-c", code],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return completed.returncode == 0


def run_setup(
    paths: SetupPaths | None = None,
    *,
    platform: SetupPlatform | None = None,
    dry_run: bool = False,
    upgrade_commandpost: bool = False,
) -> SetupResult:
    paths = paths or SetupPaths.defaults()
    platform = platform or LocalPlatform()
    result = SetupResult()
    python = paths.repo_root / ".venv/bin/python"
    if not python.is_file():
        python = Path(sys.executable)

    version = platform.commandpost_version(paths.applications)
    if version != COMMANDPOST_VERSION:
        if version is not None and not upgrade_commandpost:
            raise SetupError(
                f"CommandPost {version} is installed; pass --upgrade-commandpost "
                f"to install {COMMANDPOST_VERSION}"
            )
        result.planned.append(f"install CommandPost {COMMANDPOST_VERSION}")
        if not dry_run:
            platform.install_commandpost(paths.applications)
            result.changed.append(f"install CommandPost {COMMANDPOST_VERSION}")
    result.checks["commandpost"] = dry_run or (
        platform.commandpost_version(paths.applications) == COMMANDPOST_VERSION
    )

    watch_ready = all(
        _watch_version(root) == WATCH_RELEASE.removeprefix("v")
        for root in (paths.codex_skills, paths.claude_skills)
    )
    if not watch_ready:
        result.planned.append(f"install watch {WATCH_RELEASE}")
        if not dry_run:
            platform.install_watch(paths)
            result.changed.append(f"install watch {WATCH_RELEASE}")
    result.checks["watch"] = dry_run or all(
        _watch_version(root) == WATCH_RELEASE.removeprefix("v")
        for root in (paths.codex_skills, paths.claude_skills)
    )

    _ensure_symlink(
        paths.codex_skills / "final-cut-editor",
        paths.repo_root / "skills/final-cut-editor",
        result,
        dry_run,
    )
    _ensure_symlink(
        paths.claude_skills / "final-cut-editor",
        paths.repo_root / "skills/final-cut-editor",
        result,
        dry_run,
    )
    _ensure_symlink(
        paths.commandpost_plugins / "editor-cli-bridge",
        paths.repo_root / "commandpost/editor-cli-bridge",
        result,
        dry_run,
    )

    _write_config(
        paths.codex_config,
        _merge_codex_config(paths.codex_config, python, paths.repo_root),
        result,
        dry_run,
    )
    _write_config(
        paths.claude_config,
        _merge_claude_config(paths.claude_config, python, paths.repo_root),
        result,
        dry_run,
    )

    if dry_run:
        result.checks["mcp"] = True
    else:
        result.checks["mcp"] = platform.verify_mcp(python, paths.repo_root)
        if not result.checks["mcp"]:
            raise SetupError("The editor-cli MCP server failed its verification")
    return result
