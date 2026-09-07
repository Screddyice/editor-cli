"""The native distribution must not depend on the retired paid controller."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_retired_runtime_is_absent():
    assert not (ROOT / "src/editor_cli/adapters/commandpost.py").exists()
    assert not (ROOT / "commandpost/editor-cli-bridge/init.lua").exists()


def test_native_helper_has_no_controller_listener():
    source = ROOT / "native/final-cut-bridge/Sources/FinalCutBridge"
    for file in source.glob("*.swift"):
        text = file.read_text()
        assert "NWListener" not in text
        assert "27480" not in text


def test_native_errors_give_operator_actions():
    source = (
        ROOT / "native/final-cut-bridge/Sources/FinalCutBridge/FinalCut.swift"
    ).read_text()
    assert "Open Final Cut Pro 12.3" in source
    assert "editor-cli permissions request" in source
