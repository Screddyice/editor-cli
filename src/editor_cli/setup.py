"""Idempotent setup for the local Final Cut controller."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
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
NATIVE_METADATA_VERSION = 1
NATIVE_MANAGED_BY = "editor-cli.native-final-cut-helper"
MCP_MANAGED_BY = "editor-cli.mcp-server"
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


@dataclass(frozen=True)
class _SymlinkPlan:
    action: str
    raw_target: str | None = None
    device: int | None = None
    inode: int | None = None


class SetupPlatform(Protocol):
    def run(self, command: tuple[str, ...], *, cwd: Path | None = None) -> None: ...

    def install_watch(self, paths: SetupPaths) -> None: ...

    def helper_has_signature(self, path: Path, identifier: str) -> bool: ...

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


def _symlink_plan(link: Path, target: Path, legacy_target: Path) -> _SymlinkPlan:
    target = target.resolve()
    if link.is_symlink():
        before = link.lstat()
        raw_target = os.readlink(link)
        after = link.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SetupError(f"Path changed during setup preflight: {link}")
        if raw_target == str(target):
            return _SymlinkPlan("current", raw_target, before.st_dev, before.st_ino)
        if raw_target == str(legacy_target.resolve()):
            return _SymlinkPlan("legacy", raw_target, before.st_dev, before.st_ino)
        raise SetupError(f"Refusing to replace existing path: {link}")
    if link.exists() or link.is_symlink():
        raise SetupError(f"Refusing to replace existing path: {link}")
    return _SymlinkPlan("create")


def _symlink_matches_plan(link: Path, plan: _SymlinkPlan) -> bool:
    if not link.is_symlink():
        return False
    try:
        before = link.lstat()
        raw_target = os.readlink(link)
        after = link.lstat()
    except OSError:
        return False
    return (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino) == (
        plan.device,
        plan.inode,
    ) and raw_target == plan.raw_target


def _atomic_exchange(source: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        function_name = "renameatx_np"
    elif sys.platform.startswith("linux"):
        function_name = "renameat2"
    else:
        raise SetupError(
            "Atomic skill migration is unavailable on this platform; "
            "the legacy link was left unchanged"
        )

    library = ctypes.CDLL(None, use_errno=True)
    try:
        exchange = getattr(library, function_name)
    except AttributeError as exc:
        raise SetupError(
            "Atomic skill migration is unavailable on this platform; "
            "the legacy link was left unchanged"
        ) from exc
    exchange.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    exchange.restype = ctypes.c_int

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    source_directory = os.open(source.parent, directory_flags)
    try:
        destination_directory = os.open(destination.parent, directory_flags)
        try:
            result = exchange(
                source_directory,
                os.fsencode(source.name),
                destination_directory,
                os.fsencode(destination.name),
                0x00000002,
            )
        finally:
            os.close(destination_directory)
    finally:
        os.close(source_directory)
    if result != 0:
        error_number = ctypes.get_errno()
        raise SetupError(f"Atomic skill migration failed: {os.strerror(error_number)}")


def _replace_legacy_symlink(link: Path, target: Path, plan: _SymlinkPlan) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    quarantine = Path(
        tempfile.mkdtemp(prefix=f".{link.name}.editor-cli-migration-", dir=link.parent)
    )
    staged = quarantine / "replacement"
    staged.symlink_to(target, target_is_directory=True)
    if not _symlink_matches_plan(link, plan):
        raise SetupError(
            f"Path changed after setup preflight: {link}; "
            f"staged replacement preserved at {staged}"
        )

    try:
        _atomic_exchange(staged, link)
    except SetupError as exc:
        raise SetupError(f"{exc}; staged replacement preserved at {staged}") from exc

    if not _symlink_matches_plan(staged, plan):
        try:
            _atomic_exchange(staged, link)
        except SetupError as exc:
            raise SetupError(
                f"Path changed after setup preflight: {link}; "
                f"the displaced path is preserved at {staged}"
            ) from exc
        raise SetupError(
            f"Path changed after setup preflight: {link}; "
            f"the staged replacement is preserved at {staged}"
        )

    _fsync_directory(link.parent)
    _fsync_directory(quarantine)
    return staged


def _ensure_symlink(
    link: Path,
    target: Path,
    plan: _SymlinkPlan,
    result: SetupResult,
    dry_run: bool,
) -> None:
    if plan.action == "current":
        if not _symlink_matches_plan(link, plan):
            raise SetupError(f"Path changed after setup preflight: {link}")
        return
    verb = "migrate" if plan.action == "legacy" else "link"
    message = f"{verb} {link} -> {target}"
    result.planned.append(message)
    if dry_run:
        return
    if plan.action == "legacy":
        result.backups.append(_replace_legacy_symlink(link, target, plan))
    else:
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(target, target_is_directory=True)
        except FileExistsError as exc:
            raise SetupError(f"Path changed after setup preflight: {link}") from exc
        _fsync_directory(link.parent)
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
    try:
        parsed = tomllib.loads(existing) if existing.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(f"Codex config is invalid TOML: {path}") from exc
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise SetupError("Codex mcp_servers must contain a TOML table")
    current = servers.get("editor-cli")

    start_count = existing.count(CODEX_BLOCK_START)
    end_count = existing.count(CODEX_BLOCK_END)
    if start_count or end_count:
        if start_count != 1 or end_count != 1:
            raise SetupError("Codex editor-cli MCP ownership marker is malformed")
        before, remainder = existing.split(CODEX_BLOCK_START, 1)
        managed_text, after = remainder.split(CODEX_BLOCK_END, 1)
        try:
            managed = tomllib.loads(managed_text)
        except tomllib.TOMLDecodeError as exc:
            raise SetupError("Codex editor-cli MCP ownership block is invalid") from exc
        managed_servers = managed.get("mcp_servers", {})
        managed_entry = (
            managed_servers.get("editor-cli")
            if isinstance(managed_servers, dict)
            else None
        )
        if (
            not isinstance(current, dict)
            or not isinstance(managed_entry, dict)
            or current.get("command") != str(python)
            or managed_entry.get("command") != str(python)
        ):
            raise SetupError(
                "Codex already has an unmanaged editor-cli MCP entry; "
                "remove or rename it"
            )
        prefix = before.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + block + after
    else:
        if current is not None:
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
        "managed_by": MCP_MANAGED_BY,
    }
    current = servers.get("editor-cli")
    if current is not None:
        if (
            not isinstance(current, dict)
            or current.get("command") != str(python)
            or current.get("managed_by") != MCP_MANAGED_BY
        ):
            raise SetupError(
                "Claude already has an unmanaged editor-cli entry; remove or rename it"
            )
        updated = {**current, **desired}
        if current == updated:
            return None
        servers["editor-cli"] = updated
    else:
        servers["editor-cli"] = desired
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_sibling_temp(path: Path, content: bytes, *, mode: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return temp


def _atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    temp = _write_sibling_temp(path, content, mode=mode)
    try:
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _parse_config_path(path: Path, parse: Callable[[str], object]) -> None:
    try:
        parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SetupError(f"Refusing to install invalid configuration: {path}") from exc


def _atomic_validated_replace(
    path: Path,
    content: bytes,
    *,
    parse: Callable[[str], object],
    mode: int = 0o600,
) -> None:
    temp = _write_sibling_temp(path, content, mode=mode)
    try:
        _parse_config_path(temp, parse)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def atomic_config_update(
    path: Path,
    content: bytes,
    *,
    parse: Callable[[str], object],
    verify: Callable[[Path], None] | None = None,
) -> Path | None:
    """Install validated config bytes and restore the fixed backup on failure."""
    existed = path.exists()
    replaced = False
    backup = None
    temp = _write_sibling_temp(path, content, mode=0o600)
    try:
        _parse_config_path(temp, parse)
        backup = backup_before_write(path)
        os.replace(temp, path)
        replaced = True
        _fsync_directory(path.parent)
        if verify is None:
            _parse_config_path(path, parse)
        else:
            verify(path)
    except Exception as exc:
        if replaced:
            if backup is not None:
                _atomic_validated_replace(path, backup.read_bytes(), parse=parse)
            elif not existed:
                path.unlink(missing_ok=True)
                _fsync_directory(path.parent)
        if isinstance(exc, SetupError):
            raise
        raise SetupError(f"Failed to update configuration: {path}") from exc
    finally:
        temp.unlink(missing_ok=True)
    return backup


def _validate_config_file(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        tomllib.loads(content)
    else:
        json.loads(content)


def _write_config(
    path: Path,
    content: str | None,
    result: SetupResult,
    dry_run: bool,
    *,
    expected: str | None,
) -> None:
    if content is None:
        return
    result.planned.append(f"configure {path}")
    if dry_run:
        return
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current != expected:
        raise SetupError(f"Config changed after setup preflight: {path}")
    parse = tomllib.loads if path.suffix == ".toml" else json.loads
    backup = atomic_config_update(
        path,
        content.encode("utf-8"),
        parse=parse,
        verify=_validate_config_file,
    )
    if backup is not None:
        result.backups.append(backup)
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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _native_metadata_content(sha256: str, source_sha256: str) -> bytes:
    return (
        json.dumps(
            {
                "managed_by": NATIVE_MANAGED_BY,
                "metadata_version": NATIVE_METADATA_VERSION,
                "protocol_version": NATIVE_PROTOCOL_VERSION,
                "sha256": sha256,
                "source_sha256": source_sha256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _native_helper_ownership(
    destination: Path,
    platform: SetupPlatform,
    *,
    known_legacy_source_sha256: str,
) -> tuple[dict[str, object], bool] | None:
    metadata = destination.with_suffix(".json")
    destination_present = destination.exists() or destination.is_symlink()
    metadata_present = metadata.exists() or metadata.is_symlink()
    if not destination_present and not metadata_present:
        return None
    error = SetupError(
        "Refusing to replace unmanaged native helper artifacts at "
        f"{destination} and {metadata}"
    )
    if not destination_present or not metadata_present:
        raise error
    if destination.is_symlink() or metadata.is_symlink():
        raise error
    try:
        if not stat.S_ISREG(destination.stat().st_mode) or not stat.S_ISREG(
            metadata.stat().st_mode
        ):
            raise error
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        raise error from exc
    if not isinstance(value, dict):
        raise error
    current_keys = {
        "managed_by",
        "metadata_version",
        "protocol_version",
        "sha256",
        "source_sha256",
    }
    legacy_keys = {"protocol_version", "sha256", "source_sha256"}
    keys = set(value)
    legacy = keys == legacy_keys
    if keys not in (current_keys, legacy_keys):
        raise error
    if legacy and value.get("source_sha256") != known_legacy_source_sha256:
        raise error
    if not legacy and (
        value.get("managed_by") != NATIVE_MANAGED_BY
        or type(value.get("metadata_version")) is not int
        or value.get("metadata_version") != NATIVE_METADATA_VERSION
    ):
        raise error
    if (
        type(value.get("protocol_version")) is not int
        or value.get("protocol_version") != NATIVE_PROTOCOL_VERSION
        or not _is_sha256(value.get("sha256"))
        or not _is_sha256(value.get("source_sha256"))
    ):
        raise error
    try:
        digest_matches = value["sha256"] == _sha256(destination)
        signature_matches = platform.helper_has_signature(
            destination, NATIVE_SIGNING_IDENTIFIER
        )
    except OSError as exc:
        raise error from exc
    if not digest_matches or not signature_matches:
        raise error
    return value, legacy


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
        if not platform.helper_has_signature(staged, NATIVE_SIGNING_IDENTIFIER):
            raise SetupError("The signed native helper failed signature verification")
        digest = _sha256(staged)
        changed = (
            not destination.is_file()
            or _sha256(destination) != digest
            or destination.stat().st_mode & 0o777 != 0o700
        )
        if changed:
            os.replace(staged, destination)
            destination.chmod(0o700)
            _fsync_directory(destination.parent)
            result.changed.append(destination)
        metadata = destination.with_suffix(".json")
        content = _native_metadata_content(digest, source_sha256)
        if not metadata.is_file() or metadata.read_bytes() != content:
            _atomic_write_bytes(metadata, content)
            result.changed.append(metadata)
    finally:
        staged.unlink(missing_ok=True)


def _installed_helper_matches(
    destination: Path, source_sha256: str, platform: SetupPlatform
) -> bool:
    ownership = _native_helper_ownership(
        destination,
        platform,
        known_legacy_source_sha256=source_sha256,
    )
    if ownership is None:
        return False
    value, legacy = ownership
    return (
        not legacy
        and value.get("managed_by") == NATIVE_MANAGED_BY
        and value.get("metadata_version") == NATIVE_METADATA_VERSION
        and value.get("protocol_version") == NATIVE_PROTOCOL_VERSION
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
    ownership = _native_helper_ownership(
        destination,
        platform,
        known_legacy_source_sha256=source_sha256,
    )
    if ownership is not None:
        value, legacy = ownership
        if (
            legacy
            and value.get("source_sha256") == source_sha256
            and destination.stat().st_mode & 0o777 == 0o700
        ):
            metadata = destination.with_suffix(".json")
            _atomic_write_bytes(
                metadata,
                _native_metadata_content(str(value["sha256"]), source_sha256),
            )
            result.changed.append(metadata)
    if _installed_helper_matches(destination, source_sha256, platform):
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
    result.checks["native_helper"] = _installed_helper_matches(
        destination, source_sha256, platform
    )
    if not result.checks["native_helper"]:
        raise SetupError("The native helper failed its installed postcondition")


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

    def helper_has_signature(self, path: Path, identifier: str) -> bool:
        verified = subprocess.run(
            ("codesign", "--verify", "--strict", str(path)),
            check=False,
            capture_output=True,
            text=True,
        )
        if verified.returncode != 0:
            return False
        details = subprocess.run(
            ("codesign", "-d", "--verbose=4", str(path)),
            check=False,
            capture_output=True,
            text=True,
        )
        return details.returncode == 0 and any(
            line == f"Identifier={identifier}" for line in details.stderr.splitlines()
        )

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

    skill_source = _resource_path(final_cut_skill())
    legacy_skill_source = (paths.repo_root / "skills/final-cut-editor").resolve()
    codex_skill = paths.codex_skills / "final-cut-editor"
    claude_skill = paths.claude_skills / "final-cut-editor"
    codex_skill_plan = _symlink_plan(codex_skill, skill_source, legacy_skill_source)
    claude_skill_plan = _symlink_plan(claude_skill, skill_source, legacy_skill_source)
    codex_original = (
        paths.codex_config.read_text(encoding="utf-8")
        if paths.codex_config.is_file()
        else None
    )
    claude_original = (
        paths.claude_config.read_text(encoding="utf-8")
        if paths.claude_config.is_file()
        else None
    )
    codex_content = _merge_codex_config(paths.codex_config, python, paths.repo_root)
    claude_content = _merge_claude_config(paths.claude_config, python, paths.repo_root)
    helper = paths.application_support / "bin" / NATIVE_HELPER_NAME
    packaged_source_sha256 = _resource_tree_sha256(native_source())
    _native_helper_ownership(
        helper,
        platform,
        known_legacy_source_sha256=packaged_source_sha256,
    )

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

    _ensure_symlink(
        codex_skill,
        skill_source,
        codex_skill_plan,
        result,
        dry_run,
    )
    _ensure_symlink(
        claude_skill,
        skill_source,
        claude_skill_plan,
        result,
        dry_run,
    )

    _write_config(
        paths.codex_config,
        codex_content,
        result,
        dry_run,
        expected=codex_original,
    )
    _write_config(
        paths.claude_config,
        claude_content,
        result,
        dry_run,
        expected=claude_original,
    )

    if dry_run:
        result.checks["mcp"] = True
    else:
        result.checks["mcp"] = platform.verify_mcp(python, paths.repo_root)
        if not result.checks["mcp"]:
            raise SetupError("The editor-cli MCP server failed its verification")
    return result
