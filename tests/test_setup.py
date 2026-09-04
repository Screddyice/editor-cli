from editor_cli.setup import watch_install_command


def test_watch_install_command_pins_shared_skill_release():
    assert watch_install_command() == (
        "npx",
        "skills",
        "add",
        "https://github.com/bradautomates/claude-video/tree/v0.2.0",
        "-g",
        "--agent",
        "claude-code",
        "codex",
        "--skill",
        "watch",
        "-y",
    )
