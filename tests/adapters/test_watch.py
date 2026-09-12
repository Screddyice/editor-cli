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


def test_watch_wrapper_preserves_fractional_timestamps(tmp_path):
    import sys

    script = tmp_path / "watch.py"
    script.write_text(
        "def format_time(value): return str(round(value))\n"
        "def extract_scene_or_uniform(*args, **kwargs): return [], {}\n"
        "def extract_at_timestamps(*args, **kwargs): return [], {}\n"
        "def main(): print(format_time(3.333333))\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "editor_cli.adapters.watch_runner", str(script)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "3.333333"
    assert "str(round(value))" in script.read_text()


def test_changed_ranges_pin_boundaries_and_interior_cues(tmp_path):
    preview = tmp_path / "preview.mov"
    preview.write_bytes(b"video")
    script = tmp_path / "watch.py"
    script.write_text("# fixture")
    runner = FakeRunner()
    WatchAdapter(script, runner).analyze(preview, tmp_path / "evidence", [(1, 3)])
    call = runner.calls[1]
    assert call[call.index("--timestamps") + 1] == "1.0,1.5,2.0,2.5,3.0"


def test_fractional_report_timestamp_survives_manifest_parsing(tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    parsed = WatchAdapter._parse_frames(
        f"- `{frame}` (t=3.333333, reason=uniform)", tmp_path, "full"
    )
    assert parsed[0]["timestamp_seconds"] == 3.333333


def test_uniform_samples_use_exact_cues_without_overwriting_pinned_frames(tmp_path):
    from types import SimpleNamespace
    from editor_cli.adapters.watch_runner import install_exact_uniform_sampling

    calls = []

    def exact(video, out, times, **kwargs):
        calls.append((out, times))
        return [{"timestamp_seconds": 3.0, "path": "exact.jpg"}], {}

    module = SimpleNamespace(
        extract_scene_or_uniform=lambda *a, **kw: (
            [{"timestamp_seconds": 3.0, "path": "late.jpg", "reason": "uniform"}],
            {},
        ),
        extract_at_timestamps=exact,
    )
    install_exact_uniform_sampling(module)
    frames, _ = module.extract_scene_or_uniform("preview.mov", tmp_path)
    assert frames[0]["path"] == "exact.jpg"
    assert frames[0]["reason"] == "uniform-exact"
    assert calls == [(tmp_path / "uniform-exact", [3.0])]
