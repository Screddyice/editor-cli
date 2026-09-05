import shutil
import subprocess
from pathlib import Path

import pytest

from editor_cli.domain.edl import EDL, Segment
from editor_cli.render.fcpxml import FCPXML_VERSION, edl_to_fcpxml

DTD_PATHS = (
    Path(
        "/Applications/Final Cut Pro.app/Contents/Frameworks/"
        "Interchange.framework/Versions/A/Resources/FCPXMLv1_14.dtd"
    ),
    Path(
        "/Applications/Final Cut Pro Creator Studio.app/Contents/Frameworks/"
        "Interchange.framework/Versions/A/Resources/FCPXMLv1_14.dtd"
    ),
)


def installed_dtd() -> Path | None:
    return next((path for path in DTD_PATHS if path.is_file()), None)


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


@pytest.mark.skipif(installed_dtd() is None, reason="FCP DTD not installed")
def test_validates_against_fcp_dtd(tmp_path):
    # Copy the DTD to a space-free path; xmllint mis-resolves the app-bundle
    # path ("Final Cut Pro.app") via --dtdvalid.
    dtd_copy = tmp_path / "FCPXMLv1_14.dtd"
    dtd = installed_dtd()
    assert dtd is not None
    shutil.copy(dtd, dtd_copy)
    xml = edl_to_fcpxml(_edl(), project_name="t")
    f = tmp_path / "t.fcpxml"
    f.write_text(xml)
    res = subprocess.run(
        ["xmllint", "--noout", "--dtdvalid", str(dtd_copy), str(f)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert res.returncode == 0, res.stderr
