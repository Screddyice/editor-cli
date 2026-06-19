import json

from editor_cli.analysis import caption_emphasis as ce


def test_tags_each_word_mapping_script_and_plain():
    gen = lambda p: json.dumps({"tags": ["plain", "plain", "script"]})
    out = ce.tag_words("i just Laughed", gen)
    assert out == [("i", "declarative"), ("just", "declarative"), ("Laughed", "accent")]


def test_lead_in_plain_payoff_script():
    gen = lambda p: '{"tags": ["plain", "script"]}'
    assert ce.tag_words("my Wrist", gen) == [("my", "declarative"), ("Wrist", "accent")]


def test_empty_text_returns_empty():
    assert ce.tag_words("   ", lambda p: "{}") == []


def test_count_mismatch_falls_back_to_overlay_style_and_reports():
    gen = lambda p: '{"tags": ["script"]}'  # 1 tag, 3 words
    errors = []
    out = ce.tag_words("Run Run Run", gen, overlay_style="cursive",
                       on_error=lambda t, e: errors.append(t))
    assert out == [("Run", "accent"), ("Run", "accent"), ("Run", "accent")]
    assert errors == ["Run Run Run"]


def test_bad_json_falls_back_to_plain_for_declarative_overlay():
    out = ce.tag_words("POV: YOU LOCKED IN", lambda p: "not json",
                       overlay_style="bold-white-uppercase")
    assert {e for _, e in out} == {"declarative"}


def test_runs_collapses_consecutive_same_emphasis():
    tagged = [("i", "declarative"), ("just", "declarative"), ("Laughed", "accent")]
    assert ce.runs(tagged) == [("declarative", ["i", "just"]), ("accent", ["Laughed"])]


def test_runs_preserves_alternation_order():
    tagged = [("my", "declarative"), ("Wrist", "accent"), ("now", "declarative")]
    assert ce.runs(tagged) == [
        ("declarative", ["my"]), ("accent", ["Wrist"]), ("declarative", ["now"]),
    ]
