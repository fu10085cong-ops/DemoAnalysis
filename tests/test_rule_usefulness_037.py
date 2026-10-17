"""037 按第四轮审计补的条件（Claude 预写，实现方不得修改）。每项一对样本，只改这一个条件。"""

import copy

import pytest

from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import detect_problems
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, C
from tests.test_moments import CFG, scenario


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


# 1. 出枪慢：击杀时也要求对手比你先打中
def test_slow_reaction_kill_requires_opponent_faster(cfg, baseline):
    slower = make_moment(kind="kill", engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .5,
                                                  "opponent_time_to_damage_s": .4})
    faster = make_moment(kind="kill", engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .5,
                                                  "opponent_time_to_damage_s": .7})
    assert "slow_reaction" in problems(slower, baseline, cfg)
    assert "slow_reaction" not in problems(faster, baseline, cfg)


# 2. 漏补：屏幕内累计至少 0.4 秒
def test_missed_trade_min_view_seconds(cfg, baseline):
    assert cfg["problems"]["missed_trade_min_view_seconds"] == 0.4

    def found(seconds):
        t = MissedTrade(round=5, tick=100, teammate=B, killer=C, distance=200, killer_traded_by_other=False,
                        killer_in_view_s=seconds, killer_first_in_view_s=0.5, alive_own=3, alive_enemy=3)
        return [h.problem for h in detect_problems(make_mm(missed_trades=[t]), baseline, cfg).hits]
    assert found(0.39) == []
    assert found(0.41) == ["missed_trade"]


# 3. 人数优势冒进：T 方未下包、只剩 1 名对手时不判
def test_overpeek_skips_t_hunting_last_enemy(cfg, baseline):
    base = dict(tags=["man_advantage"], engagement={"contact_type": "peek"})
    last = make_moment(**base, context={"side": "T", "round_time_s": 40, "alive_enemy": 1, "alive_own": 3})
    two = make_moment(**base, context={"side": "T", "round_time_s": 40, "alive_enemy": 2, "alive_own": 3})
    planted = make_moment(**base, context={"side": "T", "round_time_s": 40, "alive_enemy": 1, "alive_own": 3,
                                           "bomb_planted": True})
    assert "overpeek_in_advantage" not in problems(last, baseline, cfg)
    assert "overpeek_in_advantage" in problems(two, baseline, cfg)
    assert "overpeek_in_advantage" in problems(planted, baseline, cfg)


# 4. 准星：击杀时出枪不慢于职业 p75，准星偏了也没有代价，不判
def test_crosshair_skips_kills_without_cost(cfg, baseline):
    eng = {"crosshair_initial_offset_m": 4.0}
    fast_kill = make_moment(kind="kill", engagement=eng | {"seen_to_first_shot_s": .39})
    slow_kill = make_moment(kind="kill", engagement=eng | {"seen_to_first_shot_s": .41})
    fast_death = make_moment(engagement=eng | {"seen_to_first_shot_s": .2})
    assert "poor_crosshair_placement" not in problems(fast_kill, baseline, cfg)
    assert detect_problems(make_mm([fast_kill]), baseline, cfg).skipped["poor_crosshair_placement/no_cost"] == 1
    assert "poor_crosshair_placement" in problems(slow_kill, baseline, cfg)
    assert "poor_crosshair_placement" in problems(fast_death, baseline, cfg)  # 死亡不受影响


# 5. 换弹时被打死：弹匣至少剩三成，且换弹开始至少 0.3 秒后才阵亡
def _reload(**changes):
    death = dict(unused_grenades=[], reloading_at_death=True, ammo_before_reload=9, reload_started_s=0.5,
                 magazine_size=30) | changes
    return make_moment(death=death)


def test_died_while_reloading_fraction_and_time(cfg, baseline):
    assert cfg["problems"]["reload_min_ammo_fraction"] == 0.30
    assert cfg["problems"]["reload_min_seconds"] == 0.3
    assert "died_while_reloading" in problems(_reload(), baseline, cfg)                        # 9/30 = 30%
    assert "died_while_reloading" not in problems(_reload(ammo_before_reload=8), baseline, cfg)  # 27%
    assert "died_while_reloading" not in problems(_reload(reload_started_s=0.29), baseline, cfg)
    report = detect_problems(make_mm([_reload(magazine_size=None)]), baseline, cfg)
    assert report.hits == [] or "died_while_reloading" not in {h.problem for h in report.hits}
    assert report.skipped["died_while_reloading"] == 1  # 弹匣容量未知：计入缺失
    hit = next(h for h in detect_problems(make_mm([_reload()]), baseline, cfg).hits if h.problem == "died_while_reloading")
    assert hit.evidence["magazine_size"] == 30 and hit.evidence["ammo_before_reload"] == 9


def test_magazine_size_from_match_data():
    from cs2coach.features.moments import _Params, death_state
    overrides = {(A, t): {"is_in_reload": True} for t in range(190, 200)}
    overrides[A, 189] = {"active_weapon_ammo": 9}
    overrides[C, 50] = {"X": 1000.0, "active_weapon_ammo": 30}  # 同场另一名玩家拿 AK-47 时满弹匣
    match = scenario(extra_ticks=overrides)
    row = match.kills[match.kills["victim"] == A].iloc[0]
    state = death_state(match, _Params(match, CFG), A, 200, row)
    assert state.ammo_before_reload == 9
    assert state.magazine_size == 30
    plain = scenario()
    row = plain.kills[plain.kills["victim"] == A].iloc[0]
    assert death_state(plain, _Params(plain, CFG), A, 200, row).magazine_size is None
