from editor_cli.analysis.beat_snap import snap_cuts


BEATS = [0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8]


def test_internal_cuts_snap_to_nearest_beat():
    # cuts at 0.5 and 1.5 -> nearest beats 0.4 and 1.6
    assert snap_cuts([0.0, 0.5, 1.5, 2.8], BEATS) == [0.0, 0.4, 1.6, 2.8]


def test_first_and_last_preserved():
    out = snap_cuts([0.07, 1.1, 2.75], BEATS)
    assert out[0] == 0.07 and out[-1] == 2.75


def test_colliding_cuts_bump_to_next_beat_preserving_count():
    # 0.45 and 0.55 both snap to 0.4 -> second bumps to 0.8; same number of cuts
    out = snap_cuts([0.0, 0.45, 0.55, 2.8], BEATS)
    assert out == [0.0, 0.4, 0.8, 2.8]
    assert len(out) == 4


def test_monotonic_increasing():
    out = snap_cuts([0.0, 0.5, 0.9, 1.3, 1.7, 2.8], BEATS)
    assert all(b > a for a, b in zip(out, out[1:]))


def test_no_beats_or_too_short_returns_input():
    assert snap_cuts([0.0, 1.0, 2.0], []) == [0.0, 1.0, 2.0]
    assert snap_cuts([0.0, 2.8], BEATS) == [0.0, 2.8]
