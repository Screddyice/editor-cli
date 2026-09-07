from pathlib import Path

import pytest

from editor_cli.adapters.timeline_engine import FCPXMLTimelineEngine
from editor_cli.session.models import EditOperation, EditProgram


@pytest.fixture
def anyio_backend():
    return "asyncio"


FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.11">
  <resources>
    <format id="r1" frameDuration="1/30s" width="1920" height="1080"/>
    <asset id="r2" name="Clip" start="0s" duration="10s" hasVideo="1" hasAudio="1" format="r1"/>
    <effect id="r3" name="Basic Title" uid=".../Titles.localized/Basic Title.localized/Basic Title.moti"/>
  </resources>
  <library><event name="Event"><project name="Demo"><sequence format="r1" duration="12s"><spine>
    <asset-clip name="Clip" ref="r2" offset="0s" start="0s" duration="10s" role="dialogue" audioRole="music" videoRole="video.main">
      <marker start="1s" duration="1/30s" value="Beat"/>
      <title ref="r3" name="Hello" offset="2s" start="0s" duration="2s"><text><text-style>Hello world</text-style></text></title>
    </asset-clip>
    <gap name="Gap" offset="10s" duration="2s"/>
  </spine></sequence></project></event></library>
</fcpxml>
"""


class FakeFCPXML:
    def __init__(self):
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        args = arguments.get("args", {})
        if args.get("output_path"):
            Path(args["output_path"]).write_text(FCPXML, encoding="utf-8")
        return {"text": f"{tool}.{arguments['action']}"}


@pytest.mark.anyio
async def test_timeline_engine_returns_structured_analysis(tmp_path):
    source = tmp_path / "source.fcpxml"
    source.write_text(FCPXML, encoding="utf-8")
    engine = FCPXMLTimelineEngine(FakeFCPXML())

    analysis = await engine.analyze(source)

    assert analysis["project"] == "Demo"
    assert analysis["duration_seconds"] == 12.0
    assert analysis["clips"][0]["name"] == "Clip"
    assert analysis["gaps"][0]["duration_seconds"] == 2.0
    assert analysis["roles"] == [
        {"name": "dialogue", "clip_count": 1},
        {"name": "music", "clip_count": 1, "kind": "audio"},
        {"name": "video", "clip_count": 1, "kind": "video"},
    ]
    assert analysis["markers"][0]["name"] == "Beat"
    assert analysis["effects"][0]["name"] == "Basic Title"
    assert analysis["transcript"][0]["source"] == "transcript_pack"
    assert analysis["title_text"] == [{"text": "Hello world"}]
    assert analysis["pacing"]["clip_count"] == 1


@pytest.mark.anyio
async def test_timeline_engine_chains_operations_into_one_candidate(tmp_path):
    source = tmp_path / "source.fcpxml"
    source.write_text(FCPXML, encoding="utf-8")
    destination = tmp_path / "pass-01.fcpxml"
    client = FakeFCPXML()
    engine = FCPXMLTimelineEngine(client)
    program = EditProgram(
        operations=(
            EditOperation("edit", "fill_gaps", {}),
            EditOperation(
                "edit", "add_transition", {"clip_id": "A", "position": "end"}
            ),
        )
    )

    written = await engine.apply(source, program, destination)

    assert written == destination
    assert destination.is_file()
    assert client.calls[0][1]["args"]["filepath"] == str(source)
    assert client.calls[1][1]["args"]["filepath"].endswith("step-01.fcpxml")


@pytest.mark.anyio
async def test_timeline_engine_registers_only_a_validated_session_asset(tmp_path):
    source = tmp_path / "source.fcpxml"
    source.write_text(FCPXML, encoding="utf-8")
    media = tmp_path / "reaction.mp4"
    media.write_bytes(b"movie")
    destination = tmp_path / "registered.fcpxml"
    engine = FCPXMLTimelineEngine(FakeFCPXML())

    registration = await engine.register_media(
        source,
        media,
        destination,
        name="Reaction",
        duration_seconds=1.25,
        has_video=True,
        has_audio=False,
    )

    import xml.etree.ElementTree as ET

    asset = ET.parse(destination).find(f'.//asset[@id="{registration.asset_id}"]')
    assert asset is not None
    assert asset.get("name") == "Reaction"
    assert asset.get("duration") == "5/4s"
    assert asset.find("media-rep").get("src") == media.resolve().as_uri()


@pytest.mark.anyio
async def test_timeline_engine_registers_wav_as_audio_only(tmp_path):
    source = tmp_path / "source.fcpxml"
    source.write_text(FCPXML, encoding="utf-8")
    media = tmp_path / "music.wav"
    media.write_bytes(b"RIFF fixture")
    destination = tmp_path / "registered.fcpxml"

    registration = await FCPXMLTimelineEngine(FakeFCPXML()).register_media(
        source,
        media,
        destination,
        name="Music",
        duration_seconds=2.0,
        has_video=False,
        has_audio=True,
    )

    import xml.etree.ElementTree as ET

    asset = ET.parse(destination).find(f'.//asset[@id="{registration.asset_id}"]')
    assert asset.get("hasVideo") == "0"
    assert asset.get("hasAudio") == "1"


@pytest.mark.anyio
@pytest.mark.parametrize("duration", [0.0, float("inf"), float("nan")])
async def test_timeline_engine_rejects_invalid_registration_duration(
    tmp_path, duration
):
    source = tmp_path / "source.fcpxml"
    source.write_text(FCPXML, encoding="utf-8")
    media = tmp_path / "music.wav"
    media.write_bytes(b"RIFF fixture")
    with pytest.raises(Exception, match="duration"):
        await FCPXMLTimelineEngine(FakeFCPXML()).register_media(
            source,
            media,
            tmp_path / "registered.fcpxml",
            name="Music",
            duration_seconds=duration,
            has_video=False,
            has_audio=True,
        )
