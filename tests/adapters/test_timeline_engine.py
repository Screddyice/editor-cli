from pathlib import Path

import pytest

from editor_cli.adapters.timeline_engine import FCPXMLTimelineEngine
from editor_cli.session.models import EditOperation, EditProgram


@pytest.fixture
def anyio_backend():
    return "asyncio"


FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.11">
  <resources><format id="r1" frameDuration="1/30s" width="1920" height="1080"/></resources>
  <library><event name="Event"><project name="Demo"><sequence format="r1" duration="12s"><spine><gap name="Gap" offset="0s" duration="12s"/></spine></sequence></project></event></library>
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
    assert analysis["gaps"] == 1


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
