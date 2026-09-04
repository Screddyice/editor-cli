import pytest

from editor_cli.verification.review import (
    ReviewReport,
    combine_reports,
    parse_creative_review,
)


def test_required_check_failure_blocks_pass():
    report = ReviewReport(
        required={"remove_gaps": True, "meme_insert": False},
        observations=("Meme insert is missing at 00:12",),
    )
    assert report.verified is False
    assert report.score == 0.5


def test_parse_creative_review_requires_every_named_check():
    raw = '{"required":{"remove_gaps":true},"observations":[]}'
    with pytest.raises(ValueError, match="meme_insert"):
        parse_creative_review(raw, ("remove_gaps", "meme_insert"))


def test_parse_creative_review_rejects_malformed_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_creative_review("not json", ("remove_gaps",))


def test_combine_reports_rejects_duplicate_keys():
    technical = ReviewReport({"preview_rendered": True}, ())
    creative = ReviewReport({"preview_rendered": True}, ())
    with pytest.raises(ValueError, match="Duplicate"):
        combine_reports(technical, creative)
