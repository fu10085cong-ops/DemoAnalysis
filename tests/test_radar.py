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
                trade_kills=2, flash_assists=0, utility_damage=100, rounds_played=10,
                traded_deaths=2)
    return LobbyLine(**(data | changes))


def tier(**values):
    return {"id": "amateur_tmp", "name": "业余（临时）", "source": "合成样本",
            "metrics": {key: {"values": vals, "median": vals[len(vals)//2], "n": len(vals),
                               "p25": vals[0], "p75": vals[-1]} for key, vals in values.items()},
            "problem_rates": {}}


@pytest.mark.parametrize("metric", list(RADAR_METRICS)[:4])
@pytest.mark.parametrize("value", [0, 10, 15, 20, 30])
def test_percentile_matches_baseline(metric, value):
    sample = tier(**{metric: [10, 20, 20]})
    assert percentile(metric, value, sample) == compare(metric, value, sample)["better_than_pro_pct"]


def test_directions_empty_and_axis_mean():
    sample = tier(wasted_utility_avg=[0, 50, 100], trade_kills_per_round=[0, 1, 2])
    assert percentile("wasted_utility_avg", 0, sample) > percentile("wasted_utility_avg", 100, sample)
    axes = [{"metrics": ["wasted_utility_avg", "time_to_damage_ms"]},
            {"metrics": ["trade_kills_per_round", "traded_death_pct"]}, {"metrics": ["spray_accuracy_pct"]}]
    assert axis_values({"wasted_utility_avg": 50, "trade_kills_per_round": 1}, sample, axes) == [50, 50, None]
    assert percentile("time_to_damage_ms", 1, {"metrics": {"time_to_damage_ms": {"values": []}}}) is None


def test_player_metrics_denominators():
    values = player_metrics(line())
    assert values["trade_kills_per_round"] == .2
    assert values["utility_damage_per_round"] == 10
    assert values["traded_death_pct"] == 40
    assert values["time_to_damage_ms"] is None
    empty = player_metrics(line(rounds_played=0, deaths=0))
    assert empty["trade_kills_per_round"] is None and empty["traded_death_pct"] is None


def test_summary_uses_per_match_axis_median():
    assert median_axes([[0, None], [10, None], [100, None]], 2) == [10, None]


def test_six_configured_axes():
    assert len(load_config()["radar"]["axes"]) == 6
