"""雷达分位口径、缺失值和逐场中位数。"""

import pytest

from cs2coach.baseline import compare
from cs2coach.config import load_config
from cs2coach.features.models import LobbyLine
from cs2coach.features.radar import RADAR_METRICS, axis_values, median_axes, percentile, player_metrics
from tests.helpers.synthetic import A


def line(**changes):
    data = dict(steamid=A, name="目标玩家", team=2, is_target=True, kills=10, deaths=5,
                accuracy_pct=None, aim=None, opening_kills=0, opening_deaths=0,
                trade_kills=2, trade_kill_events=2, flash_assists=0, utility_damage=100, rounds_played=10,
                traded_deaths=2)
    return LobbyLine(**(data | changes))


def tier(**values):
    return {"id": "amateur_tmp", "name": "业余（临时）", "source": "合成样本",
            "metrics": {key: {"values": vals * 10, "median": vals[len(vals)//2], "n": len(vals) * 10,
                               "p25": vals[0], "p75": vals[-1]} for key, vals in values.items()},
            "problem_rates": {}}


@pytest.mark.parametrize("metric", list(RADAR_METRICS)[:4])
@pytest.mark.parametrize("value", [0, 10, 15, 20, 30])
def test_percentile_matches_baseline(metric, value):
    sample = tier(**{metric: [10, 20, 20]})
    assert percentile(metric, value, sample, load_config()) == compare(metric, value, sample)["better_than_pro_pct"]


def test_directions_empty_and_axis_mean():
    sample = tier(time_to_damage_ms=[0, 50, 100], trade_kill_events_per_round=[0, 1, 2])
    assert percentile("time_to_damage_ms", 0, sample, load_config()) > percentile("time_to_damage_ms", 100, sample, load_config())
    axes = [{"metrics": ["time_to_damage_ms", "crosshair_offset_m"]},
            {"metrics": ["trade_kill_events_per_round", "traded_death_pct"]}, {"metrics": ["spray_accuracy_pct"]}]
    assert axis_values({"time_to_damage_ms": 50, "trade_kill_events_per_round": 1}, sample, axes, load_config()) == [50, 50, None]
    assert percentile("time_to_damage_ms", 1, {"metrics": {"time_to_damage_ms": {"values": []}}}, load_config()) is None


def test_player_metrics_denominators():
    values = player_metrics(line(), load_config())
    assert values["trade_kill_events_per_round"] == .2
    assert values["utility_damage_per_round"] == 10
    assert values["traded_death_pct"] == 40
    assert values["time_to_damage_ms"] is None
    empty = player_metrics(line(rounds_played=0, deaths=0), load_config())
    assert empty["trade_kill_events_per_round"] is None and empty["traded_death_pct"] is None


def test_summary_uses_per_match_axis_median():
    assert median_axes([[0, None], [10, None], [100, None]], 2) == [10, None]


def test_six_configured_axes():
    assert len(load_config()["radar"]["axes"]) == 6


@pytest.mark.parametrize("metric,changes", [
    ("trade_kill_events_per_round", {"rounds_played": 9}),
    ("utility_damage_per_round", {"rounds_played": 9}),
    ("traded_death_pct", {"deaths": 1, "traded_deaths": 1}),
    ("enemies_flashed_per_flash", {"flashes_thrown": 4, "enemies_flashed_per_flash": 2}),
])
def test_small_samples_are_missing(metric, changes):
    assert player_metrics(line(**changes), load_config())[metric] is None


def test_sample_boundaries_and_tier_minimum():
    cfg = load_config()
    values = player_metrics(line(flashes_thrown=5, enemies_flashed_per_flash=2), cfg)
    assert all(values[key] is not None for key in list(RADAR_METRICS)[4:])
    small = {"metrics": {"traded_death_pct": {"values": [0] * 29, "n": 29}}}
    assert percentile("traded_death_pct", 100, small, cfg) is None
    small["metrics"]["traded_death_pct"]["values"].append(0)
    assert percentile("traded_death_pct", 100, small, cfg) == 100
    assert "wasted_utility_avg" not in RADAR_METRICS


def test_one_trade_kill_can_avenge_two_teammates():
    from cs2coach.features.lobby import lobby_line
    from tests.helpers.moments import make_mm, make_moment
    from tests.test_moments import scenario

    mm = make_mm([make_moment("kill", tags=["trade_kill"],
                             trade={"is_trade_kill": True, "avenged_teammate_count": 2})])
    result = lobby_line(scenario(), A, load_config(), mm)
    assert result.trade_kills == 2 and result.trade_kill_events == 1
