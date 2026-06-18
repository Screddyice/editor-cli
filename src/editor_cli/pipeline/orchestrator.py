"""Orchestrator — wires the stages and runs the capped style-eval loop.

All external work is reached through the ``Deps`` struct so the pipeline is
fully unit-testable with fakes; ``build_deps`` constructs the real bindings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from editor_cli.acquire.discover import trend_summary
from editor_cli.domain.edl import EDL
from editor_cli.domain.style_profile import StyleProfile

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


@dataclass
class Deps:
    resolve_reference: Callable[[str, str], str]
    analyze_style: Callable[..., StyleProfile]  # (refs, context="") -> StyleProfile
    probe: Callable[[str], dict]
    transcribe: Callable[[str], Any]  # -> object with .text
    reason_edl: Callable[..., EDL]
    render_edl: Callable[[EDL, str, bool], str]
    edl_to_fcpxml: Callable[[EDL, str, dict], str]
    evaluate: Callable[[str, StyleProfile, str], Any]  # -> object with .score, .issues
    discover: Optional[Callable[[str, int], list[str]]] = None
    sound_meta: Optional[Callable[[str], Any]] = None
    # (edl, durations) -> edl with each segment re-centered on its clip's best moment
    refine_shots: Optional[Callable[[EDL, dict], EDL]] = None


@dataclass
class EditResult:
    final_mp4: str
    fcpxml: Optional[str]
    passes: int
    score: float


def _footage_files(footage_dir: str) -> list[str]:
    return sorted(
        str(p)
        for p in Path(footage_dir).iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def _duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def run_edit(
    footage_dir: str,
    prompt: str,
    refs: list[str],
    out: str,
    deps: Deps,
    *,
    max_eval: int = 3,
    threshold: float = 0.8,
    fcpxml: bool = True,
    preview: bool = False,
    genre: Optional[str] = None,
    trend_count: int = 5,
) -> EditResult:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    trend_context = ""
    if genre and deps.discover is not None:
        discovered = deps.discover(genre, trend_count)
        refs = list(refs) + discovered
        if deps.sound_meta is not None:
            trend_context = trend_summary([deps.sound_meta(u) for u in discovered])

    ref_files = [deps.resolve_reference(r, str(out_dir / "refs")) for r in refs]
    style = deps.analyze_style(ref_files, trend_context)

    footage = _footage_files(footage_dir)
    if not footage:
        raise ValueError(f"No video files found in {footage_dir}")
    probes = {f: deps.probe(f) for f in footage}
    durations = {f: _duration(probes[f]) for f in footage}
    manifest = "\n".join(f"{f}: {durations[f]:.2f}s" for f in footage)
    transcript = "\n".join(deps.transcribe(f).text for f in footage)

    final_mp4 = str(out_dir / "final.mp4")
    fcpxml_path = str(out_dir / "timeline.fcpxml") if fcpxml else None

    feedback = ""
    passes = 0
    score = 0.0
    while True:
        passes += 1
        edl = deps.reason_edl(manifest, transcript, style, prompt, footage, feedback)
        if deps.refine_shots is not None:
            edl = deps.refine_shots(edl, durations)
        deps.render_edl(edl, final_mp4, preview)
        if fcpxml_path:
            Path(fcpxml_path).write_text(deps.edl_to_fcpxml(edl, "Editor CLI", durations))
        result = deps.evaluate(final_mp4, style, prompt)
        score = result.score
        if score >= threshold or passes >= max_eval:
            break
        feedback = "\n".join(result.issues)

    return EditResult(final_mp4=final_mp4, fcpxml=fcpxml_path, passes=passes, score=score)


def build_deps(cfg: Any, out_dir: str, fetch_opts: Any = None) -> Deps:
    """Construct real bindings from a Config."""
    from editor_cli.acquire import resolve_reference
    from editor_cli.acquire.discover import discover_genre, fetch_sound_meta
    from editor_cli.analysis import shot_select
    from editor_cli.analysis.gemini import (
        GeminiClient,
        make_gemini_generate,
        make_vision_generate,
    )
    from editor_cli.analysis.transcribe import transcribe
    from editor_cli.render import ffmpeg
    from editor_cli.render.fcpxml import edl_to_fcpxml

    gemini = GeminiClient(make_gemini_generate(cfg.gemini_api_key, cfg.gemini_model))
    vision = make_vision_generate(cfg.gemini_api_key)
    frames_dir = str(Path(out_dir) / "frames")

    def refine_shots(edl: EDL, durations: dict) -> EDL:
        return shot_select.refine_windows(
            edl,
            lambda src, n: ffmpeg.sample_frames(src, n, frames_dir),
            vision,
            durations,
        )

    return Deps(
        resolve_reference=lambda ref, od: resolve_reference(ref, od, opts=fetch_opts),
        analyze_style=gemini.analyze_style,
        probe=ffmpeg.probe,
        transcribe=lambda f: transcribe(f, cfg.elevenlabs_api_key, out_dir),
        reason_edl=gemini.reason_edl,
        render_edl=ffmpeg.render_edl,
        edl_to_fcpxml=edl_to_fcpxml,
        evaluate=gemini.evaluate,
        discover=discover_genre,
        sound_meta=fetch_sound_meta,
        refine_shots=refine_shots,
    )
