import os

import pytest

from editor_cli.render import title_style as ts


def test_accent_labels_classify_as_accent():
    for label in ["cursive", "Elegant Cursive", "accent line", "soft-script", "light italic"]:
        assert ts.classify(label) == "accent"


def test_impact_and_unknown_labels_classify_as_impact():
    for label in ["bold-white-uppercase", "HEADLINE", "callout", "", None, "whatever"]:
        assert ts.classify(label) == "impact"


def test_resolve_returns_concrete_styles():
    assert ts.resolve("cursive") is ts.ACCENT
    assert ts.resolve("bold-white-uppercase") is ts.IMPACT


def test_one_family_two_weights_invariant():
    # The whole point: never two clashing families. Same family, different faces.
    assert ts.IMPACT.family == ts.ACCENT.family == ts.FONT_FAMILY
    assert ts.IMPACT.face_index != ts.ACCENT.face_index
    assert ts.IMPACT.weight != ts.ACCENT.weight


def test_emphasis_drives_case():
    assert ts.IMPACT.uppercase is True
    assert ts.ACCENT.uppercase is False


@pytest.mark.skipif(
    not os.path.exists(ts.FONT_FILE), reason="Avenir Next only present on macOS"
)
def test_faces_actually_load_from_one_collection():
    from PIL import ImageFont

    for style in (ts.IMPACT, ts.ACCENT):
        font = ImageFont.truetype(style.font_file, 80, index=style.face_index)
        fam, _weight = font.getname()
        assert fam == ts.FONT_FAMILY
