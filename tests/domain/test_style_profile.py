import json

import pytest

from editor1.domain.style_profile import StyleProfile

SAMPLE = {
    "pacing": {"cuts_per_min": 24.0, "avg_shot_len_s": 2.5},
    "transitions": ["hard cut", "whip"],
    "automations": ["auto-captions", "zoom punch"],
    "color": {"description": "warm, high contrast", "lut": None},
    "captions": {"style": "bold-uppercase", "position": "lower-third", "font": "Helvetica"},
    "sound": {"name": "trending beat", "energy": "high", "genre": "edm", "bpm": 128},
    "vibe": "fast-paced launch hype",
}


def test_style_profile_roundtrip():
    sp = StyleProfile.from_json(json.dumps(SAMPLE))
    assert sp.to_dict() == SAMPLE


def test_missing_required_key_raises():
    bad = {k: v for k, v in SAMPLE.items() if k != "pacing"}
    with pytest.raises(ValueError):
        StyleProfile.from_dict(bad)
