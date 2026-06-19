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
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from editor_cli.domain.edl import EDL
from editor_cli.domain.style_profile import StyleProfile

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
    "resolution[w,h], segments[{{src, in, out, grade, overlays, motion, "
    "transition}}], titles[], subtitles(bool), music. Cut filler/dead-air, "
    "tighten, and order to match the style. Times are seconds.\n\n"
    "{effects}\n\n"
    "STYLE:\n{style}\n\nMANIFEST:\n{manifest}\n\nTRANSCRIPT:\n{transcript}\n\n"
    "INTENT:\n{prompt}\n{feedback}"
)

# Per-segment motion-graphics the renderer understands. Single (literal) braces —
# this is substituted into _EDL_PROMPT as a value, not re-formatted.
_EFFECTS_SPEC = (
    "Segments MAY include motion graphics, but ONLY where they serve the moment "
    "— never gratuitously:\n"
    '  "motion": {"type":"ken_burns","zoom":1.05-1.2,"direction":"in"|"out"} for '
    'static/holding shots, OR {"type":"speed","factor":N} (N>1 to trim dead time, '
    "N<1 slow-mo to emphasize a beat).\n"
    '  "transition": {"crossfade":0.3-0.8,"crossfade_style":"fade"|"wipeleft"|'
    '"dissolve"} to blend clearly-related shots, OR {"fade_in":sec} / '
    '{"fade_out":sec} to open/close. Omit transition entirely for a hard cut.'
)
_EFFECTS_INTENSITY = {
    "none": "EFFECTS: Do NOT add any motion or transitions — hard cuts only.",
    "subtle": (
        "EFFECTS (subtle — default to restraint): most cuts stay hard cuts. Reach "
        "for ken_burns only on long static holds, a gentle crossfade only between "
        "clearly related shots, and speed only to trim obvious dead-air."
    ),
    "punchy": (
        "EFFECTS (punchy): lean into motion — frequent ken_burns, speed ramps, and "
        "crossfades/wipes between scene changes for energetic, dynamic pacing."
    ),
}

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


_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_RETRYABLE_NAMES = {"ServerError", "ResourceExhausted", "ServiceUnavailable"}


def _is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in _RETRYABLE_CODES:
        return True
    return type(exc).__name__ in _RETRYABLE_NAMES


def _retry(
    call: Callable[[], Any],
    attempts: int = 4,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Retry a call on transient (5xx / 429) errors with exponential backoff."""
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if not _is_retryable(exc) or i == attempts - 1:
                raise
            last = exc
            sleep(base_delay * (2 ** i))
    assert last is not None
    raise last


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
        effects_intensity: str = "subtle",
    ) -> EDL:
        fb = f"\nPREVIOUS-ISSUES TO FIX:\n{feedback}" if feedback else ""
        guidance = _EFFECTS_INTENSITY.get(
            effects_intensity, _EFFECTS_INTENSITY["subtle"]
        )
        effects = (
            guidance
            if effects_intensity == "none"
            else f"{_EFFECTS_SPEC}\n{guidance}"
        )
        p = _EDL_PROMPT.format(
            style=style.to_json(), manifest=manifest, transcript=transcript,
            prompt=prompt, feedback=fb, effects=effects,
        )
        return self._json(p, footage or [], EDL.from_dict)

    def evaluate(self, render_path: str, style: StyleProfile, prompt: str) -> EvalResult:
        p = _EVAL_PROMPT.format(style=style.to_json(), prompt=prompt)
        return self._json(
            p, [render_path],
            lambda d: EvalResult(score=float(d["score"]), issues=list(d.get("issues", []))),
        )


@dataclass
class _Cached:
    handle: Any
    sig: tuple[int, int]  # (size, mtime_ns) — re-render of an output path invalidates


class FileUploader:
    """Upload media to the Gemini File API once, concurrently, with a hard cap.

    The Gemini files endpoint has a large fixed per-upload latency on this key
    (~50-70s regardless of size), and the orchestrator re-asks the model about
    the same footage on every eval pass. So we:

    - **cache** by ``(abspath, size, mtime_ns)`` — footage uploads once across
      the whole run; a re-rendered ``final.mp4`` (new mtime) correctly re-uploads;
    - upload distinct files **concurrently** to hide the fixed latency;
    - poll for ACTIVE with a **wall-clock timeout** and raise on **FAILED** so a
      stuck file can never hang the pipeline forever.

    All I/O is injected so the logic is unit-testable without the network.
    """

    def __init__(
        self,
        upload: Callable[[str], Any],
        get: Callable[[str], Any],
        *,
        poll_timeout: float = 180.0,
        poll_interval: float = 2.0,
        max_workers: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        stat: Callable[[str], os.stat_result] = os.stat,
    ) -> None:
        self._upload = upload
        self._get = get
        self._poll_timeout = poll_timeout
        self._poll_interval = poll_interval
        self._max_workers = max_workers
        self._sleep = sleep
        self._now = now
        self._stat = stat
        self._cache: dict[str, _Cached] = {}

    def _signature(self, path: str) -> tuple[int, int]:
        st = self._stat(path)
        return (st.st_size, st.st_mtime_ns)

    def _await_active(self, handle: Any, path: str) -> Any:
        start = self._now()
        while handle.state and "PROCESSING" in str(handle.state):
            if self._now() - start > self._poll_timeout:
                raise TimeoutError(
                    f"Gemini file processing exceeded {self._poll_timeout:.0f}s for {path}"
                )
            self._sleep(self._poll_interval)
            handle = self._get(handle.name)
        if handle.state and "FAILED" in str(handle.state):
            raise RuntimeError(f"Gemini could not process {path} (state={handle.state})")
        return handle

    def _upload_one(self, path: str) -> Any:
        key = os.path.abspath(path)
        sig = self._signature(path)
        cached = self._cache.get(key)
        if cached is not None and cached.sig == sig:
            return cached.handle
        handle = self._await_active(self._upload(path), path)
        self._cache[key] = _Cached(handle, sig)
        return handle

    def upload_all(self, paths: list[str]) -> list[Any]:
        """Return file handles for ``paths`` (order preserved), uploading the
        distinct uncached ones concurrently."""
        pending = [p for p in dict.fromkeys(os.path.abspath(p) for p in paths)
                   if self._cache.get(p) is None
                   or self._cache[p].sig != self._signature(p)]
        if pending:
            workers = min(self._max_workers, len(pending))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # _upload_one writes the cache; collecting forces completion.
                list(pool.map(self._upload_one, pending))
        return [self._upload_one(p) for p in paths]


def make_gemini_generate(
    api_key: str,
    model: str = "gemini-2.5-pro",
    *,
    poll_timeout: float = 180.0,
    max_workers: int = 4,
) -> GenerateFn:
    """Build the real google-genai generate fn (uploads videos, waits for ACTIVE)."""
    from google import genai

    client = genai.Client(api_key=api_key)
    uploader = FileUploader(
        upload=lambda p: _retry(lambda: client.files.upload(file=p)),
        get=lambda name: client.files.get(name=name),
        poll_timeout=poll_timeout,
        max_workers=max_workers,
    )

    def generate(prompt: str, files: list[str]) -> str:
        contents: list[Any] = list(uploader.upload_all(files))
        contents.append(prompt)
        resp = _retry(lambda: client.models.generate_content(model=model, contents=contents))
        return resp.text or ""

    return generate


def make_vision_generate(api_key: str, model: str = "gemini-2.5-flash") -> GenerateFn:
    """Build a vision generate fn that sends images **inline** (no Files API).

    Frame selection asks about many small still images; the Files-API upload has
    a large fixed per-file latency, so inlining the JPEG bytes (as the grading
    pass does) is dramatically faster and avoids polling for ACTIVE. Returns the
    same ``(prompt, image_paths) -> str`` contract the rest of the analysis layer
    uses, so it drops straight into the shot-moment selector.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    def generate(prompt: str, files: list[str]) -> str:
        contents: list[Any] = []
        for path in files:
            with open(path, "rb") as fh:
                contents.append(types.Part.from_bytes(data=fh.read(), mime_type="image/jpeg"))
        contents.append(prompt)
        resp = _retry(lambda: client.models.generate_content(model=model, contents=contents))
        return resp.text or ""

    return generate
