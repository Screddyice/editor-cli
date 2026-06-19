import os

import pytest

from editor_cli.render import title_style as ts


def test_accent_labels_classify_as_accent():
    for label in ["cursive", "Elegant Cursive", "accent line", "soft-script", "italic"]:
        assert ts.classify(label) == "accent"


def test_declarative_and_unknown_labels_classify_as_declarative():
    for label in ["bold-white-uppercase", "HEADLINE", "callout", "", None, "whatever"]:
        assert ts.classify(label) == "declarative"


def test_resolve_returns_concrete_styles():
    assert ts.resolve("cursive") is ts.ACCENT
    assert ts.resolve("bold-white-uppercase") is ts.DECLARATIVE


def test_two_contrasting_families_not_one():
    # The reference's device is the tension between a grotesque and a script —
    # they must be *different* families (the v4 one-family collapse was wrong).
    assert ts.DECLARATIVE.family != ts.ACCENT.family
    assert ts.DECLARATIVE.family == "Futura"
    assert ts.ACCENT.family == "Snell Roundhand"


def test_declarative_is_tracked_grotesque_accent_is_plain_script():
    assert ts.DECLARATIVE.tracking > 0      # tracked caps hook
    assert ts.ACCENT.tracking == 0
    assert ts.ACCENT.gradient is True       # reference's ice-blue sheen
    assert ts.DECLARATIVE.gradient is False


def test_source_case_is_preserved_not_forced():
    # The reference keeps caps only where the source line is caps (hook) and
    # lowercase tags lowercase — so neither treatment force-uppercases.
    assert ts.DECLARATIVE.uppercase is False
    assert ts.ACCENT.uppercase is False


@pytest.mark.skipif(
    not (os.path.exists(ts.SANS_FILE) and os.path.exists(ts.SCRIPT_FILE)),
    reason="Helvetica Neue / Snell Roundhand only present on macOS",
)
def test_both_faces_load_with_expected_family():
    from PIL import ImageFont

    sans = ImageFont.truetype(ts.SANS_FILE, 80, index=ts.SANS_INDEX)
    script = ImageFont.truetype(ts.SCRIPT_FILE, 80, index=ts.SCRIPT_INDEX)
    assert sans.getname()[0] == ts.SANS_FAMILY
    assert script.getname() == (ts.SCRIPT_FAMILY, "Bold")  # refined Bold, not Black
