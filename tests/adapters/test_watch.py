import subprocess
from pathlib import Path

from editor_cli.adapters.watch import WatchAdapter


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        out = Path(argv[argv.index("--out-dir") + 1])
        frames = out / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        frame = frames / "frame_0001.jpg"
        frame.write_bytes(b"jpeg")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "# watch: video report\n\n"
                "## Frames\n\n"
                f"- `{frame}` (t=00:05, reason=scene)\n\n"
                "## Transcript\n\n_Source: whisper (groq)._\n\n```\n"
                "[00:00:04] hello\n```\n"
            ),
            stderr="",
        )


def test_watch_builds_reusable_evidence_bundle(tmp_path):
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"video")
    script = tmp_path / "watch" / "scripts" / "watch.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture")
    runner = FakeRunner()
    adapter = WatchAdapter(script=script, runner=runner)

    bundle = adapter.analyze(
        preview, tmp_path / "evidence", changed_ranges=[(4.0, 7.5)]
    )

    assert bundle.manifest.exists()
    assert bundle.frames
    assert bundle.changed_ranges == ((4.0, 7.5),)
    assert runner.calls[0].count("--detail") == 1
    assert len(runner.calls) == 2
    assert "--start" in runner.calls[1]
    assert "--end" in runner.calls[1]


def test_watch_manifest_includes_preview_hash_and_transcript(tmp_path):
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"video")
    script = tmp_path / "watch.py"
    script.write_text("# fixture")
    adapter = WatchAdapter(script=script, runner=FakeRunner())

    bundle = adapter.analyze(preview, tmp_path / "evidence", changed_ranges=[])

    assert len(bundle.preview_sha256) == 64
    assert "hello" in bundle.transcript
    assert bundle.frames[0].timestamp_seconds == 5.0
