import json
from types import SimpleNamespace

import pytest

from editor_cli.analysis.gemini import (
    EvalResult,
    FileUploader,
    GeminiClient,
    _is_retryable,
    _retry,
)
from editor_cli.domain.edl import EDL
from editor_cli.domain.style_profile import StyleProfile

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


class _Capture:
    """Records the prompt passed to generate; returns a fixed EDL."""

    def __init__(self, payload=None):
        self.prompt = None
        self._payload = payload or EDL_JSON

    def __call__(self, prompt, files):
        self.prompt = prompt
        return json.dumps(self._payload)


def test_reason_edl_offers_effects_subtle_by_default():
    cap = _Capture()
    GeminiClient(generate=cap).reason_edl("m", "t", _style(), "edit it")
    assert "motion" in cap.prompt and "transition" in cap.prompt
    assert "ken_burns" in cap.prompt and "crossfade" in cap.prompt
    assert "subtle" in cap.prompt and "restraint" in cap.prompt


def test_reason_edl_punchy_intensity():
    cap = _Capture()
    GeminiClient(generate=cap).reason_edl(
        "m", "t", _style(), "hype it", effects_intensity="punchy"
    )
    assert "punchy" in cap.prompt and "lean into motion" in cap.prompt.lower()


def test_reason_edl_none_intensity_suppresses_effects():
    cap = _Capture()
    GeminiClient(generate=cap).reason_edl(
        "m", "t", _style(), "clean cuts", effects_intensity="none"
    )
    assert "hard cuts only" in cap.prompt.lower()
    assert "ken_burns" not in cap.prompt  # spec omitted entirely


def test_reason_edl_parses_model_chosen_effects():
    payload = {
        "fps": 30.0,
        "resolution": [1080, 1920],
        "segments": [
            {"src": "a.mp4", "in": 0.0, "out": 2.0,
             "motion": {"type": "ken_burns", "zoom": 1.1, "direction": "in"},
             "transition": {"crossfade": 0.5, "crossfade_style": "fade"}},
        ],
    }
    edl = GeminiClient(generate=_Capture(payload)).reason_edl("m", "t", _style(), "go")
    seg = edl.segments[0]
    assert seg.motion == {"type": "ken_burns", "zoom": 1.1, "direction": "in"}
    assert seg.transition == {"crossfade": 0.5, "crossfade_style": "fade"}


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


# --- FileUploader -----------------------------------------------------------

def _fake_files(seqs, uploaded):
    """Fake Gemini files API. ``seqs`` maps a path/name to a list of states the
    handle reports across upload() then successive get() calls."""
    pos: dict[str, int] = {}

    def upload(path):
        uploaded.append(path)
        seq = seqs.get(path, ["ACTIVE"])
        pos[path] = 0
        return SimpleNamespace(state=seq[0], name=path)

    def get(name):
        seq = seqs.get(name, ["ACTIVE"])
        pos[name] = min(pos.get(name, 0) + 1, len(seq) - 1)
        return SimpleNamespace(state=seq[pos[name]], name=name)

    return upload, get


def _stat_from(table):
    def stat(path):
        size, mtime = table[path]
        return SimpleNamespace(st_size=size, st_mtime_ns=mtime)
    return stat


def _uploader(seqs, uploaded, table, **kw):
    upload, get = _fake_files(seqs, uploaded)
    kw.setdefault("sleep", lambda _s: None)
    return FileUploader(upload, get, stat=_stat_from(table), **kw)


def test_uploader_caches_repeated_paths_within_and_across_calls():
    uploaded: list[str] = []
    up = _uploader({}, uploaded, {"/x/a.mp4": (10, 1)})
    up.upload_all(["/x/a.mp4", "/x/a.mp4"])
    up.upload_all(["/x/a.mp4"])
    assert uploaded == ["/x/a.mp4"]  # uploaded exactly once


def test_uploader_reuploads_when_file_changes():
    uploaded: list[str] = []
    table = {"/x/out.mp4": (10, 1)}
    up = _uploader({}, uploaded, table)
    up.upload_all(["/x/out.mp4"])
    table["/x/out.mp4"] = (20, 2)  # re-rendered → new size/mtime
    up.upload_all(["/x/out.mp4"])
    assert uploaded == ["/x/out.mp4", "/x/out.mp4"]


def test_uploader_distinct_paths_each_uploaded_in_order():
    uploaded: list[str] = []
    up = _uploader({}, uploaded, {"/x/a.mp4": (1, 1), "/x/b.mp4": (2, 2)})
    handles = up.upload_all(["/x/a.mp4", "/x/b.mp4"])
    assert sorted(uploaded) == ["/x/a.mp4", "/x/b.mp4"]
    assert [h.name for h in handles] == ["/x/a.mp4", "/x/b.mp4"]


def test_uploader_waits_for_active():
    uploaded: list[str] = []
    up = _uploader({"/x/a.mp4": ["PROCESSING", "PROCESSING", "ACTIVE"]},
                   uploaded, {"/x/a.mp4": (1, 1)})
    handles = up.upload_all(["/x/a.mp4"])
    assert handles[0].state == "ACTIVE"


def test_uploader_poll_timeout_raises():
    uploaded: list[str] = []
    clock = {"v": 0.0}

    def now():
        v = clock["v"]
        clock["v"] += 100.0
        return v

    up = _uploader({"/x/a.mp4": ["PROCESSING"]}, uploaded, {"/x/a.mp4": (1, 1)},
                   poll_timeout=180.0, now=now)
    with pytest.raises(TimeoutError):
        up.upload_all(["/x/a.mp4"])


def test_uploader_failed_state_raises():
    uploaded: list[str] = []
    up = _uploader({"/x/a.mp4": ["FAILED"]}, uploaded, {"/x/a.mp4": (1, 1)})
    with pytest.raises(RuntimeError):
        up.upload_all(["/x/a.mp4"])
