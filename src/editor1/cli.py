"""Editor1 CLI — thin controllers that validate input and call the pipeline."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Editor1 — AI video editor (Final Cut Pro + Gemini).",
    no_args_is_help=True,
    add_completion=False,
)


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
) -> None:
    """Edit footage into final.mp4 + timeline.fcpxml."""
    typer.echo(f"edit {footage_dir} -> {out} ({prompt!r}); refs={ref or []}")


if __name__ == "__main__":
    app()
