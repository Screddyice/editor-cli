"""ElevenLabs Scribe transcription — word-level timestamps.

Ports the request shape from references/video-use/helpers/transcribe.py: extract
mono 16kHz audio via ffmpeg, POST to Scribe, parse word-level timestamps.
``post`` and ``extract_audio`` are injectable so unit tests run offline. Results
cache as raw Scribe JSON under ``<out_dir>/transcripts/<stem>.json``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"

PostFn = Callable[..., Any]
ExtractFn = Callable[[str, str], str]


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @classmethod
    def from_scribe(cls, data: dict[str, Any]) -> "Transcript":
        words: list[Word] = []
        for w in data.get("words", []):
            if w.get("type", "word") != "word":
                continue  # skip spacing / audio_event entries
            text = w.get("text") or w.get("word") or ""
            words.append(Word(text=text, start=float(w["start"]), end=float(w["end"])))
        return cls(words=words)


def _extract_audio(video_path: str, out_wav: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", out_wav],
        check=True, capture_output=True,
    )
    return out_wav


def transcribe(
    video_path: str,
    api_key: str,
    out_dir: Optional[str] = None,
    *,
    post: Optional[PostFn] = None,
    extract_audio: Optional[ExtractFn] = None,
) -> Transcript:
    stem = Path(video_path).stem
    cache: Optional[Path] = None
    if out_dir:
        cache = Path(out_dir) / "transcripts" / f"{stem}.json"
        if cache.exists():
            return Transcript.from_scribe(json.loads(cache.read_text()))

    post = post or _default_post
    extract_audio = extract_audio or _extract_audio

    with tempfile.TemporaryDirectory() as td:
        audio = extract_audio(video_path, str(Path(td) / f"{stem}.wav"))
        with open(audio, "rb") as fh:
            resp = post(
                SCRIBE_URL,
                headers={"xi-api-key": api_key},
                data={
                    "model_id": "scribe_v1",
                    "diarize": "true",
                    "tag_audio_events": "true",
                    "timestamps_granularity": "word",
                },
                files={"file": fh},
            )
    resp.raise_for_status()
    data = resp.json()
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return Transcript.from_scribe(data)


def _default_post(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.post(*args, **kwargs)
