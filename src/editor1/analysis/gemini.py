"""Gemini adapter — the visual brain.

Three jobs, all returning domain objects parsed from the model's JSON:
- ``analyze_style(refs)`` → StyleProfile (Gemini watches reference videos)
- ``reason_edl(...)``    → EDL (cut decisions applying the style to the footage)
- ``evaluate(...)``      → EvalResult (score the render vs prompt + style)

The model call is injected as ``generate(prompt, video_paths) -> str`` so unit
tests run offline. ``make_gemini_generate`` builds the real google-genai impl.
One corrective retry is attempted on malformed JSON before raising.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from editor1.domain.edl import EDL
from editor1.domain.style_profile import StyleProfile

GenerateFn = Callable[[str, list[str]], str]

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_RETRY_SUFFIX = "\n\nReturn ONLY a valid JSON object. No markdown fences, no commentary."

_STYLE_PROMPT = (
    "You are a video-editing analyst. Watch the reference video(s) and return ONLY "
    "a JSON object describing the editing style with EXACTLY these keys: "
    'pacing{cuts_per_min, avg_shot_len_s}, transitions[], automations[], '
    "color{description, lut}, captions{style, position, font}, "
    "sound{name, energy, genre, bpm}, vibe. Use null where unknown."
)

_EDL_PROMPT = (
    "You are a video editor. Using the footage manifest, transcript, target style "
    "profile, and the user's intent, produce ONLY a JSON EDL with keys: fps, "
    "resolution[w,h], segments[{{src, in, out, grade, overlays}}], titles[], "
    "subtitles(bool), music. Cut filler/dead-air, tighten, and order to match the "
    "style. Times are seconds.\n\n"
    "STYLE:\n{style}\n\nMANIFEST:\n{manifest}\n\nTRANSCRIPT:\n{transcript}\n\n"
    "INTENT:\n{prompt}\n{feedback}"
)

_EVAL_PROMPT = (
    "Watch the rendered edit and score how well it matches the target style and "
    "intent. Return ONLY JSON: {{\"score\": 0.0-1.0, \"issues\": [\"...\"]}}. "
    "Be specific about pacing, bad cuts, color, and caption issues.\n\n"
    "STYLE:\n{style}\n\nINTENT:\n{prompt}"
)


@dataclass
class EvalResult:
    score: float
    issues: list[str]


def _extract_json(raw: str) -> dict[str, Any]:
    match = _FENCE.search(raw)
    text = match.group(1) if match else raw
    return json.loads(text)


class GeminiClient:
    def __init__(self, generate: GenerateFn):
        self._generate = generate

    def _json(self, prompt: str, files: list[str], parse: Callable[[dict], Any]) -> Any:
        raw = self._generate(prompt, files)
        try:
            return parse(_extract_json(raw))
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            raw = self._generate(prompt + _RETRY_SUFFIX, files)
            return parse(_extract_json(raw))

    def analyze_style(self, refs: list[str], context: str = "") -> StyleProfile:
        prompt = _STYLE_PROMPT
        if context:
            prompt = f"{_STYLE_PROMPT}\n\nADDITIONAL CONTEXT:\n{context}"
        return self._json(prompt, refs, StyleProfile.from_dict)

    def reason_edl(
        self,
        manifest: str,
        transcript: str,
        style: StyleProfile,
        prompt: str,
        footage: list[str] | None = None,
        feedback: str = "",
    ) -> EDL:
        fb = f"\nPREVIOUS-ISSUES TO FIX:\n{feedback}" if feedback else ""
        p = _EDL_PROMPT.format(
            style=style.to_json(), manifest=manifest, transcript=transcript,
            prompt=prompt, feedback=fb,
        )
        return self._json(p, footage or [], EDL.from_dict)

    def evaluate(self, render_path: str, style: StyleProfile, prompt: str) -> EvalResult:
        p = _EVAL_PROMPT.format(style=style.to_json(), prompt=prompt)
        return self._json(
            p, [render_path],
            lambda d: EvalResult(score=float(d["score"]), issues=list(d.get("issues", []))),
        )


def make_gemini_generate(api_key: str, model: str = "gemini-2.5-pro") -> GenerateFn:
    """Build the real google-genai generate fn (uploads videos, waits for ACTIVE)."""
    from google import genai

    client = genai.Client(api_key=api_key)

    def generate(prompt: str, files: list[str]) -> str:
        contents: list[Any] = []
        for path in files:
            uploaded = client.files.upload(file=path)
            while uploaded.state and "PROCESSING" in str(uploaded.state):
                time.sleep(2)
                uploaded = client.files.get(name=uploaded.name)
            contents.append(uploaded)
        contents.append(prompt)
        resp = client.models.generate_content(model=model, contents=contents)
        return resp.text or ""

    return generate
