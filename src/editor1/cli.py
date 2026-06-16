"""Editor1 CLI — thin controllers that validate input and call the pipeline."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Editor1 — AI video editor (Final Cut Pro + Gemini).",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _main() -> None:
    """Editor1 — AI video editor (Final Cut Pro + Gemini)."""


@app.command()
def edit(
    footage_dir: str = typer.Argument(..., help="Folder of raw footage to edit."),
    prompt: str = typer.Option(..., "--prompt", "-p", help="Editing intent."),
    ref: list[str] = typer.Option(
        None, "--ref", "-r", help="Reference video (local path or URL). Repeatable."
    ),
    out: str = typer.Option("edit", "--out", "-o", help="Output directory."),
    max_eval: int = typer.Option(3, "--max-eval", help="Max eval/iteration passes."),
    preview: bool = typer.Option(False, "--preview", help="Fast 720p render."),
    no_fcpxml: bool = typer.Option(False, "--no-fcpxml", help="Skip FCPXML output."),
    genre: str = typer.Option(
        None, "--genre", "-g", help="Discover trending reference videos for this genre/query."
    ),
    trend_count: int = typer.Option(5, "--trend-count", help="How many trend refs to discover."),
    cookies_from_browser: str = typer.Option(
        None, "--cookies-from-browser",
        help="Read cookies from this browser for IG/TikTok refs (chrome, safari, firefox).",
    ),
    cookies: str = typer.Option(None, "--cookies", help="Path to a cookies.txt file."),
) -> None:
    """Edit footage into final.mp4 + timeline.fcpxml."""
    from editor1.acquire import FetchOptions
    from editor1.config import ConfigError, load_config
    from editor1.pipeline.orchestrator import build_deps, run_edit

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.secho(f"Config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.echo(f"Editing {footage_dir} → {out} …")
    # References are fetched only to learn style → cap resolution and sample so
    # a long/4K reference URL doesn't break the Gemini upload. Footage is local.
    fetch_opts = FetchOptions(
        cookies_from_browser=cookies_from_browser, cookies_file=cookies,
        max_height=720, section="*0:00-240",
    )
    deps = build_deps(cfg, out, fetch_opts=fetch_opts)
    result = run_edit(
        footage_dir, prompt, ref or [], out, deps,
        max_eval=max_eval, fcpxml=not no_fcpxml, preview=preview,
        genre=genre, trend_count=trend_count,
    )
    typer.secho(
        f"✓ {result.final_mp4}  (score {result.score:.2f}, {result.passes} pass(es))",
        fg=typer.colors.GREEN,
    )
    if result.fcpxml:
        typer.echo(f"  FCP timeline: {result.fcpxml}")


@app.command()
def style(
    refs: list[str] = typer.Argument(..., help="Reference video(s): local path or URL."),
    out: str = typer.Option("style_refs", "--out", help="Download dir for URL refs."),
    cookies_from_browser: str = typer.Option(
        None, "--cookies-from-browser", help="Browser cookies for IG/TikTok refs."
    ),
    max_height: int = typer.Option(720, "--max-height", help="Cap reference download resolution."),
    sample_seconds: int = typer.Option(
        240, "--sample-seconds",
        help="Analyze only the first N seconds (0 = full video). Long videos break the upload.",
    ),
) -> None:
    """Analyze the editing style of reference video(s) → StyleProfile JSON."""
    from editor1.acquire import FetchOptions, resolve_reference
    from editor1.analysis.gemini import GeminiClient, make_gemini_generate
    from editor1.config import ConfigError, load_config

    try:
        cfg = load_config(require_elevenlabs=False)
    except ConfigError as exc:
        typer.secho(f"Config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    section = f"*0:00-{sample_seconds}" if sample_seconds and sample_seconds > 0 else None
    opts = FetchOptions(
        cookies_from_browser=cookies_from_browser, max_height=max_height, section=section
    )
    files = [resolve_reference(r, out, opts=opts) for r in refs]
    typer.secho(
        f"Analyzing style of {len(files)} reference(s) with {cfg.gemini_model} …",
        fg=typer.colors.CYAN, err=True,
    )
    gemini = GeminiClient(make_gemini_generate(cfg.gemini_api_key, cfg.gemini_model))
    typer.echo(gemini.analyze_style(files).to_json())


if __name__ == "__main__":
    app()
