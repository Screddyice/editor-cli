"""Configuration — API keys and resolved paths.

Keys resolve from (in order) an explicit ``env`` dict, else a discovered
``.env`` file plus the process environment. Gemini falls back to the
Cliqk-scoped key name used across this workspace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    gemini_api_key: str
    elevenlabs_api_key: str
    gemini_model: str = "gemini-2.5-pro"


@dataclass(frozen=True)
class ControllerConfig:
    session_root: Path
    native_helper: Path = Path(
        "~/Library/Application Support/Editor CLI/bin/editor-fcp-bridge"
    )
    native_protocol_version: int = 1
    native_action_timeout_seconds: int = 120
    fcpxml_command: tuple[str, ...] = ("uvx", "fcp-mcp-server==0.22.1")
    max_passes: int = 3


def _lexical_absolute(path: Path) -> Path:
    """Make a path absolute without following its final or parent symlinks."""
    return Path(os.path.abspath(path.expanduser()))


def _parse_dotenv(start: Path | None = None) -> dict[str, str]:
    """Walk up from ``start`` (or cwd) looking for a .env; parse KEY=VALUE lines."""
    here = (start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        env_file = d / ".env"
        if env_file.is_file():
            out: dict[str, str] = {}
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
            return out
    return {}


def load_config(
    env: dict[str, str] | None = None,
    dotenv_start: Path | None = None,
    require_elevenlabs: bool = True,
) -> Config:
    if env is None:
        # Merge .env then process env, but never let an empty value clobber a
        # good one (a shell that exports KEY="" must not mask the .env value).
        src: dict[str, str] = {}
        src.update({k: v for k, v in _parse_dotenv(dotenv_start).items() if v})
        src.update({k: v for k, v in os.environ.items() if v})
    else:
        src = dict(env)

    gemini = src.get("GEMINI_API_KEY") or src.get("CLIQK_GEMINI_API_KEY")
    if not gemini:
        raise ConfigError("Missing GEMINI_API_KEY (or CLIQK_GEMINI_API_KEY)")

    elevenlabs = src.get("ELEVENLABS_API_KEY")
    if not elevenlabs and require_elevenlabs:
        raise ConfigError("Missing ELEVENLABS_API_KEY")

    model = src.get("EDITOR1_GEMINI_MODEL", "gemini-2.5-pro")
    return Config(
        gemini_api_key=gemini, elevenlabs_api_key=elevenlabs or "", gemini_model=model
    )


def load_controller_config(
    env: dict[str, str] | None = None,
) -> ControllerConfig:
    """Load local Final Cut controller settings without requiring cloud keys."""
    src = dict(os.environ if env is None else env)
    root = Path(src.get("EDITOR_CLI_SESSION_ROOT", "~/Movies/Editor CLI Sessions"))
    helper = Path(
        src.get(
            "EDITOR_CLI_NATIVE_HELPER",
            "~/Library/Application Support/Editor CLI/bin/editor-fcp-bridge",
        )
    )
    try:
        native_timeout = int(src.get("EDITOR_CLI_NATIVE_ACTION_TIMEOUT_SECONDS", "120"))
    except ValueError as exc:
        raise ConfigError(
            "EDITOR_CLI_NATIVE_ACTION_TIMEOUT_SECONDS must be an integer"
        ) from exc
    if native_timeout <= 0 or native_timeout > 3_600:
        raise ConfigError(
            "EDITOR_CLI_NATIVE_ACTION_TIMEOUT_SECONDS must be between 1 and 3,600"
        )
    max_passes = int(src.get("EDITOR_CLI_MAX_PASSES", "3"))
    if max_passes != 3:
        raise ConfigError("EDITOR_CLI_MAX_PASSES must be 3 for the initial release")

    return ControllerConfig(
        session_root=root.expanduser().resolve(),
        native_helper=_lexical_absolute(helper),
        native_protocol_version=1,
        native_action_timeout_seconds=native_timeout,
        max_passes=max_passes,
    )
