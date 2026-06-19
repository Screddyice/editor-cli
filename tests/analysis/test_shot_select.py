import json

import pytest

from editor_cli.analysis import shot_select as ss
from editor_cli.domain.edl import EDL, Segment


def _frames(times):
    return [(t, f"f{i}.jpg") for i, t in enumerate(times)]


def test_best_moment_returns_chosen_frames_timestamp():
    frames = _frames([0.0, 1.0, 2.0, 3.0])
    analyze = lambda prompt, paths: json.dumps({"index": 2, "reason": "peak"})
    assert ss.best_moment(frames, analyze) == 2.0


def test_best_moment_passes_all_frame_images():
    frames = _frames([0.0, 0.5, 1.0])
    seen = {}

    def analyze(prompt, paths):
        seen["paths"] = paths
        seen["prompt"] = prompt
        return '{"index": 0}'

    ss.best_moment(frames, analyze)
    assert seen["paths"] == ["f0.jpg", "f1.jpg", "f2.jpg"]
    assert "t=0.50s" in seen["prompt"]


def test_best_moment_retries_once_on_bad_json_then_succeeds():
    frames = _frames([0.0, 1.0])
    calls = []

    def analyze(prompt, paths):
        calls.append(prompt)
        return "garbage, not json" if len(calls) == 1 else '{"index": 1}'

    assert ss.best_moment(frames, analyze) == 1.0
    assert len(calls) == 2 and ss._RETRY_SUFFIX in calls[1]


def test_best_moment_empty_frames_raises():
    with pytest.raises(ValueError):
        ss.best_moment([], lambda p, f: "{}")


def test_recenter_centers_window_on_moment():
    assert ss.recenter(2.0, 5.0, 20.0) == (4.0, 6.0)


def test_recenter_clamps_to_clip_bounds():
    assert ss.recenter(2.0, 0.1, 20.0) == (0.0, 2.0)       # near start
    assert ss.recenter(2.0, 19.9, 20.0) == (18.0, 20.0)    # near end


def _edl():
    return EDL(
        fps=30.0,
        resolution=(1080, 1920),
        segments=[
            Segment("a.mp4", 0.0, 1.0, grade="warm", overlays=[{"text": "hi"}]),
            Segment("b.mp4", 5.0, 6.0),
        ],
        titles=[{"text": "T"}],
        music={"src": "m.mp3"},
    )


def test_refine_windows_recenters_each_segment_and_preserves_metadata():
    def sample(src, n):
        return _frames([0.0, 4.0, 8.0]) if src == "a.mp4" else _frames([0.0, 10.0, 20.0])

    # always pick the middle frame (4.0s for a, 10.0s for b)
    analyze = lambda p, f: '{"index": 1}'
    durations = {"a.mp4": 9.0, "b.mp4": 21.0}

    out = ss.refine_windows(_edl(), sample, analyze, durations)
    a, b = out.segments
    assert (a.in_, a.out) == (3.5, 4.5)        # centered on 4.0, dur 1.0
    assert a.grade == "warm" and a.overlays == [{"text": "hi"}]
    assert (b.in_, b.out) == (9.5, 10.5)       # centered on 10.0
    assert out.titles == [{"text": "T"}] and out.music == {"src": "m.mp3"}


def test_refine_windows_falls_back_and_reports_on_failure():
    def sample(src, n):
        if src == "b.mp4":
            raise RuntimeError("ffmpeg blew up")
        return _frames([0.0, 4.0, 8.0])

    analyze = lambda p, f: '{"index": 1}'
    skipped = []
    out = ss.refine_windows(
        _edl(), sample, analyze, {"a.mp4": 9.0, "b.mp4": 21.0},
        on_skip=lambda src, exc: skipped.append((src, str(exc))),
    )
    # a refined, b kept its original window, failure surfaced (not silent)
    assert (out.segments[0].in_, out.segments[0].out) == (3.5, 4.5)
    assert (out.segments[1].in_, out.segments[1].out) == (5.0, 6.0)
    assert skipped == [("b.mp4", "ffmpeg blew up")]
