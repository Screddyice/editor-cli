"""Direct editing commands. The agent uses the same service as MCP."""

import json
from pathlib import Path

import typer

from editor_cli.direct.session import dispatch

app = typer.Typer(help="Edit selected files to MP4 with Codex or Claude, without Final Cut.")


def invoke(action: str, session_dir: str | None = None, **kwargs):
    try:
        result = dispatch(action, session_dir, **kwargs)
    except (ValueError, RuntimeError, OSError, TypeError) as exc:
        typer.echo(f"Direct edit failed: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(result, indent=2))


@app.command()
def doctor():
    """Check direct rendering and transcription availability."""
    invoke("doctor")


@app.command()
def start(
    files: list[str] = typer.Option(..., "--file", "-f", help="Exact selected file. Repeat for each file."),
    prompt: str = typer.Option(..., "--prompt", "-p"),
):
    """Snapshot the exact selected files and return a resumable session."""
    invoke("start", files=files, prompt=prompt)


@app.command("run")
def run_action(
    action: str,
    session_dir: str,
    data: str = typer.Option("{}", "--data", help="Action arguments as a JSON object."),
    data_file: Path | None = typer.Option(None, "--data-file", help="Explicit JSON arguments file."),
):
    """Inspect, transcribe, approve, render, review, export, or finish a session."""
    try:
        arguments = json.loads(data_file.read_text() if data_file else data)
        if not isinstance(arguments, dict):
            raise ValueError("Action data must be a JSON object")
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    invoke(action, session_dir, **arguments)


@app.command()
def setup():
    """Install the direct-video-editor skill for Codex and Claude Code."""
    from editor_cli.direct.setup import install_skills
    try:
        result = install_skills()
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(result, indent=2))
