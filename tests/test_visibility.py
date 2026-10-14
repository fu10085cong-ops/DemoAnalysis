"""视野计算与 Leetify 口径的枪法统计。

场景：x = 500 处有一堵墙（覆盖 y < -50）。C 站在 (1000, 0)。A 原本躲在 (0, -200)（被墙挡住），
tick 80 走到 (0, 0)（能看到 C）。A 朝 +X（yaw 0）。
"""

import copy

import pandas as pd
import pytest

from cs2coach.config import load_config
from cs2coach.features.moments import find_moments
from cs2coach.features.visibility import Sight, angle_between
from cs2coach.parsing.match import build_damages, build_grenades, build_kills, build_shots
from tests.helpers.synthetic import A, B, C, D, WallChecker, make_match, make_round, make_ticks, sid
from tests.test_moments import bullet, death, fire, hurt

CFG = load_config()


def ticks(a_pos=None, a_yaw=None, c_yaw=None, extra=None):
    o = {}
    for t in range(1, 121):
        y = (a_pos or {}).get(t, -200.0 if t < 80 else 0.0)
        o[(A, t)] = {"Y": y, "yaw": (a_yaw or {}).get(t, 0.0)}
        o[(C, t)] = {"X": 1000.0, "yaw": (c_yaw or {}).get(t, 180.0)}
        o[(B, t)] = {"X": -3000.0, "Y": -3000.0}
        o[(D, t)] = {"X": -3000.0, "Y": 3000.0}
    o.update(extra or {})
    return make_ticks(overrides=o)


def sight(m, smokes=None):
    if smokes is not None:
        m.grenades = build_grenades(smokes, m)
    return Sight(m, CFG, checker=WallChecker(wall_x=500.0, wall_y_max=-50.0))


def test_first_seen_after_peek():
    m = make_match(ticks())
    fs = sight(m).first_seen(A, C, 95, floor=10)
    assert fs.tick == 80 and not fs.seen_at_limit and not fs.smoke


def test_target_outside_field_of_view_is_not_seen():
    m = make_match(ticks(a_yaw={t: 180.0 for t in range(1, 121)}))  # A 背对 C
    fs = sight(m).first_seen(A, C, 95, floor=10)
    assert fs.tick is None and not fs.seen_at_limit


def test_short_occlusion_is_tolerated_long_one_is_not():
    short = make_match(ticks(a_pos={85: -200.0}))  # 只被挡住 1 个 tick（≤ first_seen_gap_ticks）
    assert sight(short).first_seen(A, C, 95, floor=10).tick == 80
    long = make_match(ticks(a_pos={t: -200.0 for t in range(84, 88)}))  # 被挡住 4 个 tick
    assert sight(long).first_seen(A, C, 95, floor=10).tick == 88


def test_seen_for_whole_lookback_has_no_start():
    m = make_match(ticks(a_pos={t: 0.0 for t in range(1, 121)}))
    fs = sight(m).first_seen(A, C, 95, floor=10)
    assert fs.tick is None and fs.seen_at_limit


def test_not_seen_at_event():
    m = make_match(ticks(a_pos={t: -200.0 for t in range(1, 121)}))  # 一直被墙挡住（比如穿墙击杀）
    fs = sight(m).first_seen(A, C, 95, floor=10)
    assert fs.tick is None and not fs.seen_at_limit


def test_smoke_at_first_seen_marks_unreliable():
    m = make_match(ticks())
    smoke = pd.DataFrame([{"tick": 10, "user_steamid": sid(B), "entityid": 1, "x": 500.0, "y": 0.0, "z": 0.0}])
    s = sight(m, {"smokegrenade_detonate": smoke})
    assert s.first_seen(A, C, 95, floor=10).smoke
    # 烟刚展开（不到 smoke_active_seconds 的下限）时不算
    young = pd.DataFrame([{"tick": 70, "user_steamid": sid(B), "entityid": 1, "x": 500.0, "y": 0.0, "z": 0.0}])
    assert not sight(make_match(ticks()), {"smokegrenade_detonate": young}).first_seen(A, C, 95, floor=10).smoke


def test_angle_between_view_vectors():
    m = make_match(ticks(a_yaw={80: 10.0}))
    s = sight(m)
    assert angle_between(s.view_vector(A, 80), s.view_vector(A, 95)) == pytest.approx(10.0, abs=0.01)
    assert angle_between(None, s.view_vector(A, 95)) is None


# ---------- 接入交火与整场统计 ----------


def duel(cfg=CFG, checker=None, c_yaw=None):
    m = make_match(ticks(a_yaw={80: 10.0}, c_yaw=c_yaw), rounds=[make_round(freeze_end=10, end=110, last=120)])
    m.kills = build_kills(pd.DataFrame([death(100, A, C)]), m)
    m.damages = build_damages(pd.DataFrame([hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0)]), m)
    fires = [fire(90, A), fire(95, A), fire(100, A)]
    m.shots = build_shots(pd.DataFrame(fires), pd.DataFrame([bullet(f["tick"], A) for f in fires]), m)
    s = Sight(m, cfg, checker=checker or WallChecker(wall_x=500.0, wall_y_max=-50.0))
    return find_moments(m, A, cfg, sight=s)


def test_engagement_visibility_metrics():
    # C 先背对 A，tick 88 才转过来：A 先看见
    mm = duel(c_yaw={t: 0.0 for t in range(1, 88)})
    e = mm.moments[0].engagement
    assert e.player_first_seen_tick == 80 and e.opponent_first_seen_tick == 88
    assert e.seen_first_lead_s == pytest.approx(8 / 64, abs=1e-3)
    assert e.time_to_damage_s == pytest.approx(15 / 64, abs=1e-3)  # 80 看见 → 95 第一次伤害
    assert e.seen_to_first_shot_s == pytest.approx(10 / 64, abs=1e-3)
    assert e.first_shot_to_damage_s == pytest.approx(5 / 64, abs=1e-3)
    assert e.damage_to_kill_s == pytest.approx(5 / 64, abs=1e-3)
    assert e.crosshair_placement_deg == pytest.approx(10.0, abs=0.01)
    assert "saw_opponent_first" in mm.moments[0].tags
    assert mm.moments[0].jump_tick == max(1, 80 - 5 * 64)


def test_aim_stats_leetify_style():
    cfg = copy.deepcopy(CFG)
    cfg["aim_stats"]["min_samples"] = 1
    st = duel(cfg).aim_stats
    assert st.time_to_damage_ms == round(15 / 64 * 1000) and st.time_to_damage_samples == 1
    assert st.crosshair_placement_deg == pytest.approx(10.0, abs=0.01)
    assert (st.spotted_shots, st.spotted_accuracy_pct) == (3, pytest.approx(66.7, abs=0.1))
    assert (st.spray_shots, st.spray_accuracy_pct) == (3, pytest.approx(66.7, abs=0.1))  # 90、95、100 连续三发
    assert (st.counter_strafing_shots, st.counter_strafing_pct) == (3, 100.0)
    assert (st.hits, st.headshot_accuracy_pct) == (2, 0.0)


def test_aim_stats_need_enough_samples():
    st = duel().aim_stats  # 默认 min_samples = 5
    assert st.time_to_damage_ms is None and st.spotted_accuracy_pct is None


def test_without_map_data_visibility_metrics_are_empty():
    class Broken:
        def is_visible(self, a, b):
            raise AssertionError("不应调用")

    m = make_match(ticks())
    m.kills = build_kills(pd.DataFrame([death(100, A, C)]), m)
    m.damages = build_damages(pd.DataFrame([hurt(100, A, C, 100, 0)]), m)
    m.shots = build_shots(pd.DataFrame([fire(100, A)]), pd.DataFrame([bullet(100, A)]), m)
    s = Sight(m, CFG, checker=Broken())
    s.checker, s.unavailable_reason = None, "FileNotFoundError: no mesh"
    mm = find_moments(m, A, CFG, sight=s)
    assert mm.moments[0].engagement.visibility_note == "no_map_data"
    assert mm.aim_stats is None and mm.visibility_unavailable.startswith("FileNotFoundError")
