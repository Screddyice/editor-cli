import shutil
import subprocess
from pathlib import Path

import pytest

from editor1.domain.edl import EDL, Segment
from editor1.render.fcpxml import FCPXML_VERSION, edl_to_fcpxml

DTD = (
    "/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework/"
    "Versions/A/Resources/FCPXMLv1_14.dtd"
)


def _edl():
    return EDL(
        fps=30.0,
        resolution=(1080, 1920),
        segments=[
            Segment(src="/abs/a.mov", in_=1.0, out=3.0),
            Segment(src="/abs/b.mov", in_=0.0, out=1.5),
        ],
    )


def test_structure_and_version():
    xml = edl_to_fcpxml(_edl(), project_name="t")
    assert f'version="{FCPXML_VERSION}"' in xml
    assert "<spine>" in xml and "asset-clip" in xml
    assert "6000/3000s" in xml  # the 2.0s clip @30fps
    assert "file:///abs/a.mov" in xml
    # two unique sources => two assets
    assert xml.count("<asset ") == 2


@pytest.mark.skipif(not Path(DTD).exists(), reason="FCP DTD not installed")
def test_validates_against_fcp_dtd(tmp_path):
    # Copy the DTD to a space-free path; xmllint mis-resolves the app-bundle
    # path ("Final Cut Pro.app") via --dtdvalid.
    dtd_copy = tmp_path / "FCPXMLv1_14.dtd"
    shutil.copy(DTD, dtd_copy)
    xml = edl_to_fcpxml(_edl(), project_name="t")
    f = tmp_path / "t.fcpxml"
    f.write_text(xml)
    res = subprocess.run(
        ["xmllint", "--noout", "--dtdvalid", str(dtd_copy), str(f)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
