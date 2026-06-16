import json

import pytest

from editor1.analysis.gemini import EvalResult, GeminiClient, _is_retryable, _retry
from editor1.domain.edl import EDL
from editor1.domain.style_profile import StyleProfile

STYLE_JSON = {
    "pacing": {"cuts_per_min": 24.0, "avg_shot_len_s": 2.5},
    "transitions": ["hard cut"],
    "automations": ["auto-captions"],
    "color": {"description": "warm", "lut": None},
    "captions": {"style": "bold", "position": "lower-third", "font": None},
    "sound": {"name": None, "energy": "high", "genre": "edm", "bpm": 128},
    "vibe": "punchy",
}
EDL_JSON = {
    "fps": 30.0,
    "resolution": [1080, 1920],
    "segments": [{"src": "a.mp4", "in": 0.0, "out": 2.0}],
}


def _style():
    return StyleProfile.from_dict(STYLE_JSON)


def test_analyze_style_parses_into_domain():
    gc = GeminiClient(generate=lambda p, f: "```json\n" + json.dumps(STYLE_JSON) + "\n```")
    sp = gc.analyze_style(["ref.mp4"])
    assert isinstance(sp, StyleProfile)
    assert sp.vibe == "punchy"


def test_analyze_style_injects_context_into_prompt():
    seen = {}

    def gen(p, f):
        seen["prompt"] = p
        return json.dumps(STYLE_JSON)

    gc = GeminiClient(generate=gen)
    gc.analyze_style(["r.mp4"], context="TREND: fast cuts, EDM drop")
    assert "TREND: fast cuts, EDM drop" in seen["prompt"]


def test_reason_edl_parses_into_domain():
    gc = GeminiClient(generate=lambda p, f: json.dumps(EDL_JSON))
    edl = gc.reason_edl("manifest", "transcript", _style(), "make it punchy")
    assert isinstance(edl, EDL)
    assert len(edl.segments) == 1


def test_evaluate_parses_score_and_issues():
    gc = GeminiClient(generate=lambda p, f: json.dumps({"score": 0.8, "issues": ["x"]}))
    res = gc.evaluate("out.mp4", _style(), "prompt")
    assert isinstance(res, EvalResult)
    assert res.score == 0.8 and res.issues == ["x"]


def test_retry_on_bad_json_then_succeeds():
    calls = {"n": 0}

    def gen(p, f):
        calls["n"] += 1
        return "not json" if calls["n"] == 1 else json.dumps({"score": 0.5, "issues": []})

    gc = GeminiClient(generate=gen)
    res = gc.evaluate("o.mp4", _style(), "p")
    assert calls["n"] == 2 and res.score == 0.5


def test_raises_after_retry_still_bad():
    gc = GeminiClient(generate=lambda p, f: "still not json")
    with pytest.raises(json.JSONDecodeError):
        gc.evaluate("o.mp4", _style(), "p")


class _Transient(Exception):
    code = 503


class _Permanent(Exception):
    code = 400


def test_is_retryable_classifies():
    assert _is_retryable(_Transient())
    assert not _is_retryable(_Permanent())
    assert _is_retryable(type("ServerError", (Exception,), {})())


def test_retry_succeeds_after_transient():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Transient()
        return "ok"

    assert _retry(call, attempts=4, sleep=lambda s: None) == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_after_attempts():
    with pytest.raises(_Transient):
        _retry(lambda: (_ for _ in ()).throw(_Transient()), attempts=2, sleep=lambda s: None)


def test_retry_non_retryable_raises_immediately():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise _Permanent()

    with pytest.raises(_Permanent):
        _retry(call, attempts=4, sleep=lambda s: None)
    assert calls["n"] == 1
