from pathlib import Path

import pytest

from editor_cli.adapters.final_cut_control import CommandPostFinalCutControl
from editor_cli.session.models import ProjectIdentity


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeCommandPost:
    def __init__(self):
        self.calls = []

    def controller_message(self, action, **parameters):
        return {"action": action, "parameters": parameters}

    async def request(self, message):
        self.calls.append(message)
        if message["action"] == "active_project":
            return {
                "result": {
                    "project": "Demo",
                    "durationSeconds": 12.0,
                    "libraryPaths": ["/tmp/Canary Library.fcpbundle"],
                }
            }
        if message["action"] == "export_xml":
            destination = Path(message["parameters"]["destination"])
            destination.write_text("<fcpxml/>", encoding="utf-8")
        return {"result": {"ok": True}}


class FakeFCPXML:
    def __init__(self):
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        return {"text": "Sent to Final Cut Pro"}


@pytest.mark.anyio
async def test_final_cut_control_identifies_one_active_project():
    control = CommandPostFinalCutControl(
        FakeCommandPost(),
        FakeFCPXML(),
        library_reader=lambda: [
            {
                "name": "Canary Library",
                "events": [{"name": "Canary Event", "projects": ["Demo"]}],
            }
        ],
    )

    projects = await control.active_projects()

    assert projects == (
        ProjectIdentity("Canary Library", "Canary Event", "Demo", 12.0),
    )


@pytest.mark.anyio
async def test_final_cut_control_exports_only_to_the_requested_file(tmp_path):
    commandpost = FakeCommandPost()
    control = CommandPostFinalCutControl(commandpost, FakeFCPXML(), library_reader=list)
    destination = tmp_path / "source.fcpxml"

    await control.export_xml(
        ProjectIdentity("Library", "Event", "Demo", 12.0), destination
    )

    assert destination.is_file()
    assert commandpost.calls[-1]["action"] == "export_xml"


@pytest.mark.anyio
async def test_final_cut_control_imports_into_the_identified_library(tmp_path):
    commandpost = FakeCommandPost()
    fcpxml = FakeFCPXML()
    control = CommandPostFinalCutControl(
        commandpost,
        fcpxml,
        library_reader=lambda: [
            {
                "name": "Canary Library",
                "events": [{"name": "Canary Event", "projects": ["Demo"]}],
            }
        ],
    )
    await control.active_projects()
    candidate = tmp_path / "candidate.fcpxml"
    candidate.write_text(
        '<fcpxml version="1.11"><project name="Demo"/></fcpxml>',
        encoding="utf-8",
    )

    await control.import_project(candidate, "Demo - AI Pass 1")

    tool, arguments = fcpxml.calls[-1]
    assert tool == "deliver"
    assert arguments["action"] == "push_to_fcp"
    assert (
        Path(arguments["args"]["library_location"])
        == Path("/tmp/Canary Library.fcpbundle").resolve()
    )
    assert arguments["args"]["confirm_unreviewed"] is True
    imported = Path(arguments["args"]["filepath"])
    assert 'name="Demo - AI Pass 1"' in imported.read_text(encoding="utf-8")
