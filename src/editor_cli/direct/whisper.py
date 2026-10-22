"""Local word transcription with whisper.cpp, used instead of ElevenLabs Scribe when configured."""

import json
import os
import shutil
import subprocess
from pathlib import Path


MODEL_ENV = "EDITOR_CLI_WHISPER_MODEL"
DEFAULT_MODEL = Path.home() / ".cache" / "whisper-cpp" / "ggml-small.en.bin"


def whisper_model() -> Path | None:
    configured = os.environ.get(MODEL_ENV, "")
    if not configured:
        return DEFAULT_MODEL if DEFAULT_MODEL.is_file() else None
    model = Path(configured).expanduser()
    if not model.is_file():
        raise ValueError(f"{MODEL_ENV} does not point to a model file: {model}")
    return model


def whisper_binary() -> str | None:
    return shutil.which("whisper-cli")


def words_from_whisper(data: dict) -> dict:
    """Convert one-word-per-segment whisper.cpp JSON into the Scribe transcript shape."""
    words = []
    for segment in data.get("transcription", []):
        text = segment.get("text", "").strip()
        # whisper.cpp marks control tokens such as [_BEG_] with brackets.
        if not text or text.startswith("[_"):
            continue
        offsets = segment["offsets"]
        words.append(
            {
                "text": text,
                "start": offsets["from"] / 1000,
                "end": offsets["to"] / 1000,
                "type": "word",
            }
        )
    return {"text": " ".join(word["text"] for word in words), "words": words}


def run_whisper(audio: Path, model: Path, work: Path) -> dict:
    binary = whisper_binary()
    if binary is None:
        raise RuntimeError("whisper-cli is not installed")
    prefix = work / "whisper"
    subprocess.run(
        [
            binary,
            "-m",
            str(model),
            "-f",
            str(audio),
            "-ojf",
            "-ml",
            "1",
            "-sow",
            "-np",
            "-of",
            str(prefix),
        ],
        check=True,
        capture_output=True,
    )
    return json.loads(prefix.with_suffix(".json").read_text())
