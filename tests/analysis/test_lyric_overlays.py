from dataclasses import dataclass

from editor_cli.analysis.lyric_overlays import Phrase, group_phrases


@dataclass
class W:
    text: str
    start: float
    end: float


def _words():
    # "Tay Keith" intro, then two lyric lines with a vocal gap between them
    return [
        W("Tay", 0.04, 0.24), W("Keith", 0.26, 0.62),
        W("Just", 2.46, 2.58), W("cool,", 2.64, 2.86),
        W("its", 3.26, 3.44), W("calm.", 3.48, 3.86),
    ]


def test_groups_by_vocal_gap_and_drops_intro():
    ph = group_phrases(_words(), gap=0.34, intro_end=2.2)
    assert [p.text for p in ph] == ["Just cool", "its calm"]


def test_phrase_starts_on_first_word_and_runs_to_next():
    ph = group_phrases(_words(), gap=0.34, intro_end=2.2)
    assert ph[0].start == 2.46
    assert ph[0].end == ph[1].start == 3.26   # first phrase ends as the next begins


def test_last_phrase_holds_past_final_word():
    ph = group_phrases(_words(), gap=0.34, intro_end=2.2, tail_hold=0.5)
    assert ph[-1].end == round(3.86 + 0.5, 2)


def test_lead_in_shifts_starts_earlier_clamped_at_zero():
    ph = group_phrases(_words(), gap=0.34, intro_end=2.2, lead_in=0.15)
    assert ph[0].start == round(2.46 - 0.15, 2)


def test_strips_trailing_punctuation_from_text():
    ph = group_phrases(_words(), gap=0.34, intro_end=2.2)
    assert "," not in ph[0].text and "." not in ph[1].text


def test_empty_when_no_lyric_after_intro():
    assert group_phrases([W("Tay", 0.0, 0.2)], intro_end=2.2) == []
