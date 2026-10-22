import pytest

from editor_cli.direct.session import validate_transcript
from editor_cli.direct.whisper import whisper_model, words_from_whisper


def _segment(text, start_ms, end_ms):
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}}


def test_word_segments_become_scribe_shaped_words():
    data = {
        "transcription": [
            _segment("[_BEG_]", 0, 0),
            _segment(" And", 220, 330),
            _segment(" so", 330, 450),
            _segment("   ", 450, 460),
            _segment(" country.", 460, 1200),
        ]
    }

    transcript = words_from_whisper(data)

    assert transcript["words"] == [
        {"text": "And", "start": 0.22, "end": 0.33, "type": "word"},
        {"text": "so", "start": 0.33, "end": 0.45, "type": "word"},
        {"text": "country.", "start": 0.46, "end": 1.2, "type": "word"},
    ]
    assert transcript["text"] == "And so country."


def test_output_passes_the_same_validation_as_scribe():
    data = {"transcription": [_segment(" hello", 0, 500)]}

    validated = validate_transcript(words_from_whisper(data), total=1.0)

    assert validated["words"][0]["text"] == "hello"


def test_missing_model_path_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR_CLI_WHISPER_MODEL", str(tmp_path / "absent.bin"))
    with pytest.raises(ValueError, match="EDITOR_CLI_WHISPER_MODEL"):
        whisper_model()


def test_model_path_must_exist_when_set(monkeypatch, tmp_path):
    model = tmp_path / "ggml-small.en.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("EDITOR_CLI_WHISPER_MODEL", str(model))
    assert whisper_model() == model


def test_doctor_reports_local_transcription_without_scribe_key(monkeypatch, tmp_path):
    from editor_cli.direct.session import DirectSession

    model = tmp_path / "ggml-small.en.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("EDITOR_CLI_WHISPER_MODEL", str(model))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("EDITOR_CLI_ENV_FILE", str(tmp_path / "none.env"))

    assert DirectSession.doctor()["transcription_ready"] is True


def test_default_model_location_used_when_env_unset(monkeypatch, tmp_path):
    from editor_cli.direct import whisper

    model = tmp_path / "ggml-small.en.bin"
    model.write_bytes(b"x")
    monkeypatch.delenv("EDITOR_CLI_WHISPER_MODEL", raising=False)
    monkeypatch.setattr(whisper, "DEFAULT_MODEL", model)
    assert whisper_model() == model


def test_no_model_anywhere_means_none(monkeypatch, tmp_path):
    from editor_cli.direct import whisper

    monkeypatch.delenv("EDITOR_CLI_WHISPER_MODEL", raising=False)
    monkeypatch.setattr(whisper, "DEFAULT_MODEL", tmp_path / "absent.bin")
    assert whisper_model() is None
