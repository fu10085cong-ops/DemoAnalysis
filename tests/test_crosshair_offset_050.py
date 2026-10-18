"""预瞄初始偏差与准星问题共用样本口径（050）。"""

import pandas as pd
import pytest

from cs2coach.baseline import EXCLUDED_TAGS
from cs2coach.config import load_config
from cs2coach.features.aim_stats import aim_stats
from cs2coach.features.crosshair import crosshair_offset_sample
from cs2coach.features.models import AimStats
from cs2coach.features.problems import detect_problems
from cs2coach.features.radar import RADAR_METRICS, axis_values, player_metrics, sample_count
from cs2coach.parsing.match import build_damages, build_shots
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, C, make_match
from tests.test_moments import bullet, fire, hurt
from tests.test_radar import line, tier
from tests.test_visibility import sight, ticks
from tests.test_summary import stats


CASES = [
    ({}, True),
    ({"weapon": "mp9"}, False),
    ({"context": {"distance": 4.9}}, False),
    ({"context": {"distance": 5.0}}, True),
    ({"context": {"distance": None}}, True),
    ({"engagement": {"airborne_near_seen": True}}, False),
    ({"engagement": {"opponent_in_sight_before_view_ticks": load_config()["problems"]["crosshair_turn_min_ticks"]}}, False),
    ({"engagement": {"opponent_in_sight_before_view_ticks": load_config()["problems"]["crosshair_turn_min_ticks"] - 1}}, True),
    ({"through_smoke": True}, False),
    ({"engagement": {"shots_before_seen": load_config()["problems"]["max_shots_before_seen"]}}, False),
    ({"kind": "assist"}, False),
    ({"engagement": None}, False),
    *[({"tags": [tag]}, False) for tag in sorted(EXCLUDED_TAGS)],
    *[({"engagement": {"crosshair_initial_offset_m": value}}, False)
      for value in (None, float("nan"), float("inf"), -float("inf"))],
]


def moment(**changes):
    engagement = {"crosshair_initial_offset_m": 100.0, "seen_to_first_shot_s": .8}
    if changes.get("kind") != "assist" and isinstance(changes.get("engagement", {}), dict):
        changes["engagement"] = engagement | changes.get("engagement", {})
    return make_moment(**changes)


@pytest.mark.parametrize("changes,eligible", CASES)
@pytest.mark.parametrize("kind", ["death", "kill"])
def test_samples_and_problem_detection_agree(changes, eligible, kind):
    cfg = load_config()
    m = moment(**({"kind": kind} | changes))
    value = crosshair_offset_sample(m, cfg)
    assert value == 100.0 if eligible else value is None
    baseline = {"weapons": {"ak47": {
        "crosshair_initial_offset_m": {"p75": 1.0}, "seen_to_first_shot_ms": {"p75": 500.0}}}}
    report = detect_problems(make_mm([m]), baseline, cfg)
    detected = any(hit.problem == "poor_crosshair_placement" for hit in report.hits)
    assert (detected or report.skipped.get("poor_crosshair_placement/no_cost", 0) > 0) == eligible


def test_no_cost_remains_a_sample_and_exclusions_follow_config():
    cfg = load_config()
    m = moment(kind="kill", engagement={"seen_to_first_shot_s": .1})
    baseline = {"weapons": {"ak47": {
        "crosshair_initial_offset_m": {"p75": 1.0}, "seen_to_first_shot_ms": {"p75": 500.0}}}}
    report = detect_problems(make_mm([m]), baseline, cfg)
    assert report.skipped["poor_crosshair_placement/no_cost"] == 1
    assert crosshair_offset_sample(m, cfg) == 100.0
    m.through_smoke = True
    assert crosshair_offset_sample(m, cfg) is None
    cfg["problems"]["exclusions"]["through_smoke"].remove("poor_crosshair_placement")
    assert crosshair_offset_sample(m, cfg) == 100.0


@pytest.mark.parametrize("n,expected", [(4, None), (5, 1.23), (6, 1.73)])
def test_aim_stats_aggregates_only_eligible_offsets(n, expected):
    cfg = load_config()
    match = make_match(ticks())
    match.damages = build_damages(pd.DataFrame([hurt(95, A, C, 30, 70)]), match)
    match.shots = build_shots(pd.DataFrame([fire(90, A)]), pd.DataFrame([bullet(90, A)]), match)
    moments = [moment(engagement={"crosshair_initial_offset_m": x})
               for x in [0.5, 1.0, 1.234, 2.234, 3.5, 4.0][:n]]
    moments.append(moment(through_smoke=True))
    result = aim_stats(match, A, cfg, match.shots, sight(match), moments)
    assert result.crosshair_offset_samples == n
    assert result.crosshair_offset_m == expected
    legacy = AimStats.model_validate(result.model_dump(exclude={"crosshair_offset_m", "crosshair_offset_samples"}))
    assert legacy.crosshair_offset_m is None and legacy.crosshair_offset_samples == 0


def test_radar_uses_offset_and_old_tier_is_empty():
    cfg = load_config()
    aim = stats(100).model_copy(update={"crosshair_offset_m": 1.03, "crosshair_offset_samples": 5})
    player = line(aim=aim)
    assert RADAR_METRICS["crosshair_offset_m"] is True
    assert list(RADAR_METRICS)[1] == "crosshair_offset_m"
    assert sample_count(player, "crosshair_offset_m") == 5
    assert player_metrics(player, cfg)["crosshair_offset_m"] == 1.03
    old_tier = tier(crosshair_placement_deg=[1, 2, 3])
    assert axis_values(player_metrics(player, cfg), old_tier, cfg["radar"]["axes"], cfg)[1] is None
    player.aim.crosshair_offset_samples = 4
    assert player_metrics(player, cfg)["crosshair_offset_m"] is None
