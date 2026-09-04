import json
import subprocess
from pathlib import Path

from editor_cli.verification.technical import inspect_preview


class FakeMediaRunner:
    def __init__(self, probe, black_stderr="", silence_stderr="", silence_code=0):
        self.probe = probe
        self.black_stderr = black_stderr
        self.silence_stderr = silence_stderr
        self.silence_code = silence_code

    def __call__(self, argv, timeout):
        if argv[0] == "ffprobe":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.probe), stderr="")
        is_black = "blackdetect" in " ".join(argv)
        stderr = self.black_stderr if is_black else self.silence_stderr
        return subprocess.CompletedProcess(
            argv, 0 if is_black else self.silence_code, stdout="", stderr=stderr
        )


def test_technical_probe_rejects_black_or_missing_preview():
    runner = FakeMediaRunner({"streams": [], "format": {"duration": "0"}})
    report = inspect_preview(Path("preview.mp4"), runner=runner)
    assert report.required["readable_video"] is False
    assert report.required["nonzero_duration"] is False


def test_technical_probe_detects_black_and_unexpected_silence():
    runner = FakeMediaRunner(
        {
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080},
                {"codec_type": "audio"},
            ],
            "format": {"duration": "8.0"},
        },
        black_stderr="black_start:1 black_end:2 black_duration:1",
        silence_stderr="silence_start:3\nsilence_end:5 | silence_duration:2",
    )
    report = inspect_preview(Path("preview.mp4"), runner=runner)
    assert report.required["no_unexpected_black"] is False
    assert report.required["no_unexpected_silence"] is False


def test_technical_probe_checks_expected_duration_and_fcpxml_qc():
    runner = FakeMediaRunner(
        {
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
            "format": {"duration": "7.0"},
        }
    )
    report = inspect_preview(
        Path("preview.mp4"),
        runner=runner,
        expected_duration=8.0,
        duration_tolerance=0.1,
        fcpxml_qc=False,
    )
    assert report.required["duration_matches"] is False
    assert report.required["fcpxml_valid"] is False


def test_video_only_preview_does_not_invent_unexpected_silence():
    runner = FakeMediaRunner(
        {
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
            "format": {"duration": "8.0"},
        },
        silence_stderr="Stream specifier ':a' matches no streams",
        silence_code=1,
    )
    report = inspect_preview(Path("preview.mp4"), runner=runner)
    assert report.required["no_unexpected_silence"] is True
