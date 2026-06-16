"""Configuration — API keys and resolved paths.

Keys resolve from (in order) an explicit ``env`` dict, else a discovered
``.env`` file plus the process environment. Gemini falls back to the
Cliqk-scoped key name used across this workspace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    gemini_api_key: str
    elevenlabs_api_key: str
    gemini_model: str = "gemini-2.5-pro"


def _parse_dotenv(start: Optional[Path] = None) -> dict[str, str]:
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
    env: Optional[dict[str, str]] = None,
    dotenv_start: Optional[Path] = None,
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
    if not elevenlabs:
        raise ConfigError("Missing ELEVENLABS_API_KEY")

    model = src.get("EDITOR1_GEMINI_MODEL", "gemini-2.5-pro")
    return Config(gemini_api_key=gemini, elevenlabs_api_key=elevenlabs, gemini_model=model)
