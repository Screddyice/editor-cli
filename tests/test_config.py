from pathlib import Path

import pytest

from editor_cli.config import Config, ConfigError, load_config, load_controller_config


def test_load_config_reads_keys():
    cfg = load_config(env={"GEMINI_API_KEY": "g", "ELEVENLABS_API_KEY": "e"})
    assert isinstance(cfg, Config)
    assert cfg.gemini_api_key == "g"
    assert cfg.elevenlabs_api_key == "e"


def test_gemini_falls_back_to_cliqk_key():
    cfg = load_config(env={"CLIQK_GEMINI_API_KEY": "g2", "ELEVENLABS_API_KEY": "e"})
    assert cfg.gemini_api_key == "g2"


def test_elevenlabs_optional_when_not_required():
    cfg = load_config(env={"GEMINI_API_KEY": "g"}, require_elevenlabs=False)
    assert cfg.gemini_api_key == "g"
    assert cfg.elevenlabs_api_key == ""


def test_missing_key_raises_named_error():
    with pytest.raises(ConfigError) as exc:
        load_config(env={"GEMINI_API_KEY": "g"})
    assert "ELEVENLABS_API_KEY" in str(exc.value)


def test_controller_config_needs_no_cloud_keys(tmp_path: Path):
    cfg = load_controller_config(
        env={"EDITOR_CLI_SESSION_ROOT": str(tmp_path / "sessions")}
    )
    assert cfg.session_root == (tmp_path / "sessions").resolve()
    assert cfg.commandpost_url == "ws://127.0.0.1:27480/"
    assert cfg.max_passes == 3


def test_controller_config_rejects_non_loopback_commandpost():
    with pytest.raises(ConfigError, match="loopback"):
        load_controller_config(
            env={"EDITOR_CLI_COMMANDPOST_URL": "ws://192.168.1.50:27480/"}
        )
