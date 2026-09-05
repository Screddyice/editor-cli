"""Idempotent setup for the local Final Cut controller."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Protocol

from editor_cli.resources import final_cut_skill, native_source

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


WATCH_RELEASE = "v0.2.0"
NATIVE_PROTOCOL_VERSION = 1
NATIVE_HELPER_NAME = "editor-fcp-bridge"
NATIVE_BUILD_PRODUCT = "FinalCutBridge"
NATIVE_SIGNING_IDENTIFIER = "com.screddy.editorcli.finalcutbridge"
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
    application_support: Path = field(
        default_factory=lambda: Path(
            "~/Library/Application Support/Editor CLI"
        ).expanduser()
    )

    @classmethod
    def defaults(cls, repo_root: Path | None = None) -> SetupPaths:
        root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        return cls(
            repo_root=root,
            codex_config=Path("~/.codex/config.toml").expanduser(),
            claude_config=Path("~/.claude.json").expanduser(),
            codex_skills=Path("~/.codex/skills").expanduser(),
            claude_skills=Path("~/.claude/skills").expanduser(),
        )


@dataclass
class SetupResult:
    changed: list[str | Path] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


class SetupPlatform(Protocol):
    def run(self, command: tuple[str, ...], *, cwd: Path | None = None) -> None: ...

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
            return line.partition(":")[2].strip().strip("\"'")
    return None


def _resource_path(resource: Traversable) -> Path:
    path = Path(str(resource))
    if not path.exists():
        raise SetupError(f"Packaged resource is unavailable: {resource}")
    return path.resolve()


def _ensure_symlink(
    link: Path, target: Path, result: SetupResult, dry_run: bool
) -> None:
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
        updated = (
            existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
        )
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
    if not isinstance(servers, dict):
        raise SetupError("Claude mcpServers must contain a JSON object")
    desired = {
        "type": "stdio",
        "command": str(python),
        "args": ["-m", "editor_cli.mcp_server"],
        "cwd": str(repo_root),
    }
    current = servers.get("editor-cli")
    if current == desired:
        return None
    if current is not None:
        raise SetupError(
            "Claude already has an unmanaged editor-cli entry; remove or rename it"
        )
    servers["editor-cli"] = desired
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(mode)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _validate_config_file(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        tomllib.loads(content)
    else:
        json.loads(content)


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
    existed = path.exists()
    try:
        _atomic_write_bytes(path, content.encode("utf-8"))
        _validate_config_file(path)
    except Exception:
        if backup is not None:
            _atomic_write_bytes(path, backup.read_bytes())
        elif not existed:
            path.unlink(missing_ok=True)
        raise
    result.changed.append(f"configure {path}")


def _copy_resource_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_resource_tree(child, target)
        elif child.is_file():
            with child.open("rb") as source_file, target.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)


def _resource_tree_sha256(source: Traversable) -> str:
    digest = hashlib.sha256()

    def add_tree(node: Traversable, prefix: str) -> None:
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                add_tree(child, relative)
            elif child.is_file():
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")

    add_tree(source, "")
    return digest.hexdigest()


def _built_helper(source: Path) -> Path:
    direct = source / ".build" / "release" / NATIVE_BUILD_PRODUCT
    if direct.is_file():
        return direct
    matches = tuple(source.glob(f".build/*/release/{NATIVE_BUILD_PRODUCT}"))
    if len(matches) != 1:
        raise SetupError("Swift build did not produce one FinalCutBridge executable")
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install_native_helper(
    source_binary: Path,
    destination: Path,
    platform: SetupPlatform,
    result: SetupResult,
    source_sha256: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_staged = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    staged = Path(raw_staged)
    try:
        shutil.copyfile(source_binary, staged)
        staged.chmod(0o700)
        platform.run(
            (
                "codesign",
                "--force",
                "--sign",
                "-",
                "--identifier",
                NATIVE_SIGNING_IDENTIFIER,
                str(staged),
            )
        )
        digest = _sha256(staged)
        changed = not destination.is_file() or _sha256(destination) != digest
        if changed:
            os.replace(staged, destination)
            destination.chmod(0o700)
            _fsync_directory(destination.parent)
            result.changed.append(destination)
        metadata = destination.with_suffix(".json")
        content = (
            json.dumps(
                {
                    "protocol_version": NATIVE_PROTOCOL_VERSION,
                    "sha256": digest,
                    "source_sha256": source_sha256,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if not metadata.is_file() or metadata.read_bytes() != content:
            _atomic_write_bytes(metadata, content)
            result.changed.append(metadata)
    finally:
        staged.unlink(missing_ok=True)


def _installed_helper_matches(destination: Path, source_sha256: str) -> bool:
    metadata = destination.with_suffix(".json")
    if not destination.is_file() or not metadata.is_file():
        return False
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return (
        value.get("protocol_version") == NATIVE_PROTOCOL_VERSION
        and value.get("source_sha256") == source_sha256
        and value.get("sha256") == _sha256(destination)
        and destination.stat().st_mode & 0o777 == 0o700
    )


def _build_and_install_native_helper(
    paths: SetupPaths,
    platform: SetupPlatform,
    result: SetupResult,
    dry_run: bool,
) -> None:
    destination = paths.application_support / "bin" / NATIVE_HELPER_NAME
    result.planned.append(f"build and install {destination}")
    if dry_run:
        result.checks["native_helper"] = True
        return
    packaged_source = native_source()
    source_sha256 = _resource_tree_sha256(packaged_source)
    if _installed_helper_matches(destination, source_sha256):
        result.checks["native_helper"] = True
        return
    with tempfile.TemporaryDirectory(prefix="editor-cli-native-") as raw_temp:
        source = Path(raw_temp) / "source"
        _copy_resource_tree(packaged_source, source)
        platform.run(
            (
                "swift",
                "build",
                "--package-path",
                str(source),
                "-c",
                "release",
            )
        )
        _install_native_helper(
            _built_helper(source),
            destination,
            platform,
            result,
            source_sha256,
        )
    result.checks["native_helper"] = destination.is_file()


class LocalPlatform:
    def run(self, command: tuple[str, ...], *, cwd: Path | None = None) -> None:
        subprocess.run(
            command,
            cwd=cwd,
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
    """Build the native helper and configure both local agent hosts."""
    del upgrade_commandpost  # Retained for CLI compatibility during the migration.
    paths = paths or SetupPaths.defaults()
    platform = platform or LocalPlatform()
    result = SetupResult()
    python = paths.repo_root / ".venv/bin/python"
    if not python.is_file():
        python = Path(sys.executable)

    _build_and_install_native_helper(paths, platform, result, dry_run)

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

    skill_source = _resource_path(final_cut_skill())
    _ensure_symlink(
        paths.codex_skills / "final-cut-editor", skill_source, result, dry_run
    )
    _ensure_symlink(
        paths.claude_skills / "final-cut-editor", skill_source, result, dry_run
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
