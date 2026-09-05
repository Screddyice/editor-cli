import json
import subprocess
from pathlib import Path

import pytest

from editor_cli.verification.technical import (
    inspect_candidate,
    inspect_candidate_fcpxml,
    inspect_preview,
)


class FakeMediaRunner:
    def __init__(self, probe, black_stderr="", silence_stderr="", silence_code=0):
        self.probe = probe
        self.black_stderr = black_stderr
        self.silence_stderr = silence_stderr
        self.silence_code = silence_code

    def __call__(self, argv, timeout):
        if argv[0] == "ffprobe":
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps(self.probe), stderr=""
            )
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


def test_candidate_qc_parses_duration_and_checks_missing_media(tmp_path):
    present = tmp_path / "present.mov"
    present.write_bytes(b"media")
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(
        f'''<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
        <asset id="r2" name="Present"><media-rep src="{present.as_uri()}"/></asset>
        <asset id="r3" name="Missing"><media-rep src="{(tmp_path / "missing.mov").as_uri()}"/></asset>
        </resources><library><event name="Event"><project name="Candidate">
        <sequence format="r1" duration="7s"><spine/></sequence>
        </project></event></library></fcpxml>''',
        encoding="utf-8",
    )

    qc = inspect_candidate_fcpxml(
        candidate, upstream_validation={"text": "## Health Score: 100%"}
    )

    assert qc.duration_seconds == 7.0
    assert qc.required["fcpxml_parseable"] is True
    assert qc.required["timeline_valid"] is True
    assert qc.required["media_online"] is False
    assert str(tmp_path / "missing.mov") in "\n".join(qc.observations)


def test_candidate_qc_rejects_malformed_xml(tmp_path):
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text("<fcpxml>", encoding="utf-8")

    qc = inspect_candidate_fcpxml(
        candidate, upstream_validation={"text": "## Health Score: 100%"}
    )

    assert qc.duration_seconds is None
    assert qc.required["fcpxml_parseable"] is False
    assert qc.required["timeline_valid"] is False


def test_candidate_qc_checks_media_resources_not_only_asset_resources(tmp_path):
    missing = tmp_path / "missing.mov"
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(
        f'''<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s"/>
        <media id="r2"><media-rep src="{missing.as_uri()}"/></media>
        </resources><library><event name="Event"><project name="Candidate">
        <sequence format="r1" duration="12s"><spine/></sequence>
        </project></event></library></fcpxml>''',
        encoding="utf-8",
    )

    qc = inspect_candidate_fcpxml(
        candidate, upstream_validation={"text": "## Health Score: 100%"}
    )

    assert qc.required["media_online"] is False
    assert qc.media_references == (missing,)


@pytest.mark.parametrize(
    ("resources", "timeline", "observation"),
    [
        (
            '<asset id="r2"><media-rep src="{present}"/></asset>',
            '<asset-clip ref="r404" offset="0s" duration="12s"/>',
            "unresolved media resource: r404",
        ),
        (
            '<asset id="r2"/>',
            '<asset-clip ref="r2" offset="0s" duration="12s"/>',
            "has no usable source: r2",
        ),
        (
            '<asset id="r2"><media-rep src="https://example.com/media.mov"/></asset>',
            '<asset-clip ref="r2" offset="0s" duration="12s"/>',
            "reference is unsupported",
        ),
        ("", '<gap offset="0s" duration="12s"/>', "no media sources"),
    ],
)
def test_candidate_qc_rejects_invalid_or_empty_timeline_media_graph(
    tmp_path, resources, timeline, observation
):
    present = tmp_path / "present.mov"
    present.write_bytes(b"media")
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(
        f"""<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s"/>
        {resources.format(present=present.as_uri())}
        </resources><library><event name="Event"><project name="Candidate">
        <sequence format="r1" duration="12s"><spine>{timeline}</spine></sequence>
        </project></event></library></fcpxml>""",
        encoding="utf-8",
    )

    qc = inspect_candidate_fcpxml(
        candidate, upstream_validation={"text": "## Health Score: 100%"}
    )

    assert qc.required["media_online"] is False
    assert observation in "\n".join(qc.observations)


def test_candidate_qc_resolves_nested_media_resources(tmp_path):
    present = tmp_path / "present.mov"
    present.write_bytes(b"media")
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(
        f'''<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s"/>
        <asset id="r2"><media-rep src="{present.as_uri()}"/></asset>
        <media id="r3"><sequence format="r1" duration="12s"><spine>
        <asset-clip ref="r2" offset="0s" duration="12s"/>
        </spine></sequence></media>
        </resources><library><event name="Event"><project name="Candidate">
        <sequence format="r1" duration="12s"><spine>
        <ref-clip ref="r3" offset="0s" duration="12s"/>
        </spine></sequence>
        </project></event></library></fcpxml>''',
        encoding="utf-8",
    )

    qc = inspect_candidate_fcpxml(
        candidate, upstream_validation={"text": "## Health Score: 100%"}
    )

    assert qc.required["media_online"] is True
    assert qc.media_references == (present,)


def test_technical_qc_uses_candidate_duration_instead_of_source_duration(tmp_path):
    media = tmp_path / "source.mov"
    media.write_bytes(b"media")
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(
        f"""<fcpxml version="1.11"><resources>
        <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
        <asset id="r2"><media-rep src="{media.as_uri()}"/></asset>
        </resources><library><event name="Event"><project name="Candidate">
        <sequence format="r1" duration="12s"><spine>
        <asset-clip ref="r2" offset="0s" duration="12s"/>
        </spine></sequence>
        </project></event></library></fcpxml>""",
        encoding="utf-8",
    )
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    runner = FakeMediaRunner(
        {
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
            "format": {"duration": "12.0"},
        }
    )

    result = inspect_candidate(
        candidate,
        preview,
        expected_source_duration=20.0,
        upstream_validation={"text": "## Health Score: 100%"},
        runner=runner,
    )

    assert result.expected_duration == 12.0
    assert result.fcpxml_valid is True
    assert result.report.required["duration_matches"] is True
