import pytest

from editor_cli.config import Config, ConfigError, load_config


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
