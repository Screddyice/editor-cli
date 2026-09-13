import json

import pytest

from editor_cli.session.models import EvidenceBinding
from editor_cli.verification.review import (
    ReviewReport,
    combine_reports,
    parse_creative_review,
)


def binding() -> EvidenceBinding:
    return EvidenceBinding(
        session_id="a" * 32,
        pass_number=1,
        state_version=7,
        project_name="Demo - aaaaaaaa - AI Pass 1",
        candidate_sha256="1" * 64,
        preview_sha256="2" * 64,
        manifest_sha256="3" * 64,
        frame_timestamps=(1.0, 2.0),
    )


def test_required_check_failure_blocks_pass():
    report = ReviewReport(
        required={"remove_gaps": True, "meme_insert": False},
        observations=("Meme insert is missing at 00:12",),
    )
    assert report.verified is False
    assert report.score == 0.5


def test_parse_creative_review_requires_every_named_check():
    raw = json.dumps(
        {
            "required": {"remove_gaps": True},
            "observations": [],
            "binding": binding().to_dict(),
        }
    )
    with pytest.raises(ValueError, match="meme_insert"):
        parse_creative_review(
            raw, ("remove_gaps", "meme_insert"), expected_binding=binding()
        )


def test_parse_creative_review_rejects_malformed_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_creative_review("not json", ("remove_gaps",))


def test_parse_creative_review_binds_exact_candidate_and_evidence():
    expected = binding()
    payload = {
        "required": {"remove_gaps": True},
        "observations": [],
        "changed_ranges": [[1.0, 2.0]],
        "binding": expected.to_dict(),
    }

    report = parse_creative_review(
        json.dumps(payload), ("remove_gaps",), expected_binding=expected
    )

    assert report.binding == expected
    payload["binding"]["preview_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="preview hash"):
        parse_creative_review(
            json.dumps(payload), ("remove_gaps",), expected_binding=expected
        )


def test_parse_creative_review_rejects_unknown_top_level_fields():
    payload = {
        "required": {"remove_gaps": True},
        "observations": [],
        "binding": binding().to_dict(),
        "trust_me": True,
    }
    with pytest.raises(ValueError, match="unexpected fields"):
        parse_creative_review(
            json.dumps(payload), ("remove_gaps",), expected_binding=binding()
        )


@pytest.mark.parametrize("value", [1, 0, "true", None, []])
def test_parse_creative_review_requires_strict_boolean_results(value):
    expected = binding()
    payload = {
        "required": {"gap_removed": value},
        "observations": [],
        "binding": expected.to_dict(),
    }

    with pytest.raises(ValueError, match="booleans"):
        parse_creative_review(
            json.dumps(payload), ("gap_removed",), expected_binding=expected
        )


def test_combine_reports_rejects_duplicate_keys():
    technical = ReviewReport({"preview_rendered": True}, ())
    creative = ReviewReport({"preview_rendered": True}, ())
    with pytest.raises(ValueError, match="Duplicate"):
        combine_reports(technical, creative)
