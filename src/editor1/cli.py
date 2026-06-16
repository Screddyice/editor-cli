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
) -> None:
    """Edit footage into final.mp4 + timeline.fcpxml."""
    from editor1.config import ConfigError, load_config
    from editor1.pipeline.orchestrator import build_deps, run_edit

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.secho(f"Config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.echo(f"Editing {footage_dir} → {out} …")
    deps = build_deps(cfg, out)
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


if __name__ == "__main__":
    app()
