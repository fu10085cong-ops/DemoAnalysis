"""035 按第三轮审计"值不值得提醒"补的条件（Claude 预写，实现方不得修改）。每项一对样本，只改这一个条件。"""

import copy

import pytest

from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import detect_problems
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import B, C


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def baseline():
    stats = {"time_to_damage_ms": {"p75": 500.0}, "seen_to_first_shot_ms": {"p75": 400.0},
             "crosshair_placement_deg": {"p75": 10.0}, "crosshair_initial_offset_m": {"p75": 2.0},
             "distance_m": {"p90": 30.0}}
    return {"source": "手工职业样本", "weapons": {w: copy.deepcopy(stats) for w in ("ak47", "glock")}, "spray": {}}


def problems(m, baseline, cfg):
    return {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


# 1. 出枪慢：阈值加余量；击杀时要求对手打中过你
def test_slow_reaction_margin(cfg, baseline):
    assert cfg["problems"]["slow_reaction_margin_ratio"] == 0.10
    near = make_moment(engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .43})  # 只超 7.5%
    far = make_moment(engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .45})   # 超 12.5%
    assert "slow_reaction" not in problems(near, baseline, cfg)
    assert "slow_reaction" in problems(far, baseline, cfg)


def test_slow_reaction_kill_requires_opponent_damage(cfg, baseline):
    hit_back = make_moment(kind="kill", engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .5,
                                                    "opponent_time_to_damage_s": .4})
    harmless = make_moment(kind="kill", engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .5,
                                                    "opponent_time_to_damage_s": None})
    assert "slow_reaction" in problems(hit_back, baseline, cfg)
    assert "slow_reaction" not in problems(harmless, baseline, cfg)
    report = detect_problems(make_mm([harmless]), baseline, cfg)
    assert report.skipped["slow_reaction/opponent_no_damage"] == 1


# 2. 准星：余量 25%；5 米内不判
def test_crosshair_margin_and_min_distance(cfg, baseline):
    assert cfg["problems"]["crosshair_margin_ratio"] == 0.25
    assert "poor_crosshair_placement" not in problems(make_moment(engagement={"crosshair_initial_offset_m": 2.4}), baseline, cfg)
    assert "poor_crosshair_placement" in problems(make_moment(engagement={"crosshair_initial_offset_m": 2.6}), baseline, cfg)
    close = make_moment(engagement={"crosshair_initial_offset_m": 4.0}, context={"distance": 4.9})
    normal = make_moment(engagement={"crosshair_initial_offset_m": 4.0}, context={"distance": 5.0})
    assert "poor_crosshair_placement" not in problems(close, baseline, cfg)
    assert "poor_crosshair_placement" in problems(normal, baseline, cfg)


# 3. 漏补：只剩自己时不判；击杀者 2 秒后才出现不判（配置改动）
def _trade(**changes):
    base = dict(round=5, tick=100, teammate=B, killer=C, distance=200, killer_traded_by_other=False,
                killer_in_view_s=1.0, killer_first_in_view_s=0.5, alive_own=3, alive_enemy=3)
    return MissedTrade(**(base | changes))


def test_missed_trade_last_alive_and_late_view(cfg, baseline):
    def found(t):
        return [h.problem for h in detect_problems(make_mm(missed_trades=[t]), baseline, cfg).hits]
    assert found(_trade()) == ["missed_trade"]
    assert found(_trade(alive_own=1)) == []
    assert detect_problems(make_mm(missed_trades=[_trade(alive_own=1)]), baseline, cfg).skipped["missed_trade/last_alive"] == 1
    assert found(_trade(alive_own=2)) == ["missed_trade"]
    assert cfg["problems"]["missed_trade_min_react_seconds"] == 3.0
    assert found(_trade(killer_first_in_view_s=1.9)) == ["missed_trade"]
    assert found(_trade(killer_first_in_view_s=2.1)) == []


# 4. 人数优势冒进：T 方开局 25 秒内不判
def test_overpeek_skips_t_early_round(cfg, baseline):
    base = dict(tags=["man_advantage"], engagement={"contact_type": "peek"})
    early_t = make_moment(**base, context={"side": "T", "round_time_s": 24.9})
    later_t = make_moment(**base, context={"side": "T", "round_time_s": 25.0})
    early_ct = make_moment(**base, context={"side": "CT", "round_time_s": 20.0})
    assert "overpeek_in_advantage" not in problems(early_t, baseline, cfg)
    assert "overpeek_in_advantage" in problems(later_t, baseline, cfg)
    assert "overpeek_in_advantage" in problems(early_ct, baseline, cfg)
    assert cfg["problems"]["overpeek_t_min_round_time_s"] == 25.0
