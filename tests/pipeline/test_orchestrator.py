from pathlib import Path
from types import SimpleNamespace

from editor_cli.analysis.gemini import EvalResult
from editor_cli.domain.edl import EDL, Segment
from editor_cli.domain.style_profile import StyleProfile
from editor_cli.pipeline.orchestrator import Deps, run_edit

STYLE = StyleProfile.from_dict({
    "pacing": {"cuts_per_min": 24.0, "avg_shot_len_s": 2.5},
    "transitions": ["hard cut"],
    "automations": [],
    "color": {"description": "warm", "lut": None},
    "captions": {"style": "bold", "position": "lower", "font": None},
    "sound": {"name": None, "energy": "high", "genre": "edm", "bpm": 120},
    "vibe": "punchy",
})


def _footage(tmp_path):
    foot = tmp_path / "footage"
    foot.mkdir()
    (foot / "a.mp4").write_bytes(b"x")
    return foot


def _deps(counter, scorer):
    def reason(*args):
        counter["n"] += 1
        return EDL(fps=30.0, resolution=(1080, 1920),
                   segments=[Segment(src="a.mp4", in_=0.0, out=2.0)])

    def render(edl, out, preview):
        Path(out).write_bytes(b"video")
        return out

    return Deps(
        resolve_reference=lambda ref, od: ref,
        analyze_style=lambda files, context="": STYLE,
        probe=lambda f: {"format": {"duration": "5.0"}},
        transcribe=lambda f: SimpleNamespace(text="words"),
        reason_edl=reason,
        render_edl=render,
        edl_to_fcpxml=lambda e, n, d: "<fcpxml/>",
        evaluate=scorer,
    )


def test_run_edit_stops_when_score_passes(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    counter = {"n": 0}
    deps = _deps(counter, scorer=lambda *a: EvalResult(0.9, []))
    res = run_edit(str(foot), "make it punchy", [], str(out), deps)
    assert res.passes == 1 and counter["n"] == 1
    assert res.score == 0.9
    assert (out / "final.mp4").exists()
    assert (out / "timeline.fcpxml").exists()


def test_run_edit_loops_until_threshold(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    counter = {"n": 0}
    scores = iter([0.4, 0.6, 0.95])
    deps = _deps(counter, scorer=lambda *a: EvalResult(next(scores), ["fix it"]))
    res = run_edit(str(foot), "p", [], str(out), deps, max_eval=3, threshold=0.8)
    assert res.passes == 3 and counter["n"] == 3
    assert res.score == 0.95


def test_run_edit_caps_at_max_eval(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    counter = {"n": 0}
    deps = _deps(counter, scorer=lambda *a: EvalResult(0.3, ["still bad"]))
    res = run_edit(str(foot), "p", [], str(out), deps, max_eval=2, threshold=0.8)
    assert res.passes == 2 and counter["n"] == 2
    assert res.score == 0.3


def test_genre_discovery_adds_refs_and_trend_context(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    counter = {"n": 0}
    seen = {}

    def analyze(files, context=""):
        seen["files"] = list(files)
        seen["context"] = context
        return STYLE

    deps = _deps(counter, scorer=lambda *a: EvalResult(0.9, []))
    deps.analyze_style = analyze
    deps.discover = lambda q, n: ["https://youtu.be/x", "https://youtu.be/y"]
    deps.sound_meta = lambda u: SimpleNamespace(title="T", track="S", artist="A", url=u)

    run_edit(str(foot), "p", ["local.mp4"], str(out), deps,
             genre="edm reels", trend_count=2)

    assert "https://youtu.be/x" in seen["files"]
    assert "local.mp4" in seen["files"]
    assert "GENRE TREND REFERENCES" in seen["context"]


def test_refine_shots_runs_between_reason_and_render(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    counter = {"n": 0}
    order = []

    deps = _deps(counter, scorer=lambda *a: EvalResult(0.9, []))
    reason, render = deps.reason_edl, deps.render_edl
    deps.reason_edl = lambda *a: (order.append("reason"), reason(*a))[1]

    def refine(edl, durations):
        order.append("refine")
        # durations come from the probe stub (5.0s) and reach the refiner
        assert durations == {str(foot / "a.mp4"): 5.0}
        return EDL(fps=edl.fps, resolution=edl.resolution,
                   segments=[Segment("a.mp4", 1.0, 3.0)])

    rendered = {}
    def render_capture(edl, o, preview):
        order.append("render")
        rendered["seg"] = (edl.segments[0].in_, edl.segments[0].out)
        return render(edl, o, preview)

    deps.refine_shots = refine
    deps.render_edl = render_capture

    run_edit(str(foot), "p", [], str(out), deps)
    assert order == ["reason", "refine", "render"]
    assert rendered["seg"] == (1.0, 3.0)  # render saw the refined window


def test_refine_shots_optional_when_unset(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    deps = _deps({"n": 0}, scorer=lambda *a: EvalResult(0.9, []))
    assert deps.refine_shots is None  # default off — backward compatible
    res = run_edit(str(foot), "p", [], str(out), deps)
    assert res.passes == 1 and (out / "final.mp4").exists()


def test_no_fcpxml_when_disabled(tmp_path):
    foot = _footage(tmp_path)
    out = tmp_path / "edit"
    counter = {"n": 0}
    deps = _deps(counter, scorer=lambda *a: EvalResult(0.9, []))
    res = run_edit(str(foot), "p", [], str(out), deps, fcpxml=False)
    assert res.fcpxml is None
    assert not (out / "timeline.fcpxml").exists()
