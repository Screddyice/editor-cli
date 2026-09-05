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
    assert (
        cfg.native_helper
        == Path("~/Library/Application Support/Editor CLI/bin/editor-fcp-bridge")
        .expanduser()
        .resolve()
    )
    assert cfg.native_protocol_version == 1
    assert cfg.native_action_timeout_seconds == 120
    assert cfg.max_passes == 3


@pytest.mark.parametrize("value", ["0", "-1", "3601", "not-an-integer"])
def test_controller_config_rejects_invalid_native_action_timeout(value):
    with pytest.raises(ConfigError, match="NATIVE_ACTION_TIMEOUT"):
        load_controller_config(env={"EDITOR_CLI_NATIVE_ACTION_TIMEOUT_SECONDS": value})


def test_controller_config_resolves_native_helper_override(tmp_path):
    cfg = load_controller_config(
        env={"EDITOR_CLI_NATIVE_HELPER": str(tmp_path / "bridge")}
    )
    assert cfg.native_helper == (tmp_path / "bridge").resolve()


def test_controller_config_preserves_lexical_native_helper_symlink(tmp_path):
    target = tmp_path / "target-helper"
    target.write_bytes(b"target")
    helper = tmp_path / "editor-fcp-bridge"
    helper.symlink_to(target)

    cfg = load_controller_config(env={"EDITOR_CLI_NATIVE_HELPER": str(helper)})

    assert cfg.native_helper == helper.absolute()
    assert cfg.native_helper != target
