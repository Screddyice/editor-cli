import pytest

from editor_cli.domain.edl import EDL, Segment


def test_edl_roundtrip():
    edl = EDL(
        fps=30.0,
        resolution=(1080, 1920),
        segments=[Segment(src="a.mp4", in_=0.0, out=2.5)],
    )
    assert EDL.from_json(edl.to_json()) == edl


def test_segment_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        Segment(src="a.mp4", in_=3.0, out=1.0)


def test_segment_duration():
    assert Segment(src="a.mp4", in_=1.0, out=3.5).duration == pytest.approx(2.5)
