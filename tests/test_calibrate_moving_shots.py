"""首枪校准的分段、区间及样本过滤。"""

import pytest

from scripts.calibrate_moving_shots import bucket, parse_edges, render, samples
from tests.helpers.moments import make_mm, make_moment, make_shot


def test_edges_and_wilson_interval():
    edges = parse_edges("0,0.34,0.45,1.01")
    assert [bucket(value, edges) for value in (0, .34, .45, 1.01)] == [0, 1, 2, None]
    with pytest.raises(ValueError):
        parse_edges("0,.45,.34")
    result = render({("ak47", 1): [1, 2]}, edges, 1, 2)
    assert "50.0% (n=2，95% CI 9.5–90.5%)" in result


def test_distance_smoke_crouch_air_and_first_seen_filter():
    before = make_shot(tick=80, seen_opponent=False, hit=True)
    seen = make_shot(tick=90, speed_ratio=.4, hit=False)
    allowed = make_moment(engagement={"shots": [before, seen]})
    smoke = make_moment(engagement={"shots": [seen], "visibility_note": "smoke"})
    through_smoke = make_moment(engagement={"shots": [seen]}, through_smoke=True)
    distant = make_moment(engagement={"shots": [seen]}, context={"distance": 31})
    crouched = make_moment(engagement={"shots": [seen.model_copy(update={"crouched": True})]})
    airborne = make_moment(engagement={"shots": [seen.model_copy(update={"airborne": True})]})
    assert list(samples(make_mm([allowed, smoke, through_smoke, distant, crouched, airborne]),
                        30, [0, .34, .45, 1.01])) == [("ak47", 1, False)]
