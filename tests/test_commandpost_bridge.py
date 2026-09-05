from pathlib import Path


def test_bridge_binds_loopback_and_restricts_handlers():
    text = Path("commandpost/editor-cli-bridge/init.lua").read_text()
    assert 'setInterface("loopback")' in text
    assert "global_applescript" not in text
    for handler in (
        "global_menuactions",
        "global_handler",
        "fcpx_videoEffect",
        "fcpx_audioEffect",
        "fcpx_generator",
        "fcpx_title",
        "fcpx_transition",
    ):
        assert handler in text


def test_bridge_has_no_shell_or_network_escape_hatches():
    text = Path("commandpost/editor-cli-bridge/init.lua").read_text()
    assert "os.execute" not in text
    assert "hs.execute" not in text
    assert "hs.task" not in text


def test_bridge_exposes_only_the_final_cut_session_actions():
    text = Path("commandpost/editor-cli-bridge/init.lua").read_text()
    for action in (
        "active_project",
        "export_xml",
        "duplicate_project",
        "open_project",
    ):
        assert f"{action} = true" in text
    assert "payload.parameters" in text
    assert "durationSeconds" in text
