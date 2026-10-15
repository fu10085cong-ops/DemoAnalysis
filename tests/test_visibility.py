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


def test_shots_before_seen_are_split_out():
    # A 在 tick 70（还被墙挡着）就朝 C 的方向开了一枪：TTD 的两段只从看见后的第一枪算
    m = make_match(ticks(a_yaw={80: 10.0}), rounds=[make_round(freeze_end=10, end=110, last=120)])
    m.kills = build_kills(pd.DataFrame([death(100, A, C)]), m)
    m.damages = build_damages(pd.DataFrame([hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0)]), m)
    fires = [fire(70, A), fire(90, A), fire(95, A), fire(100, A)]
    m.shots = build_shots(pd.DataFrame(fires), pd.DataFrame([bullet(f["tick"], A) for f in fires]), m)
    e = find_moments(m, A, CFG, sight=Sight(m, CFG, checker=WallChecker(500.0, -50.0))).moments[0].engagement
    assert e.shots_fired == 4 and e.shots_before_seen == 1
    assert e.seen_to_first_shot_s == pytest.approx(10 / 64, abs=1e-3)
    assert e.first_shot_to_damage_s == pytest.approx(5 / 64, abs=1e-3)
    assert e.seen_to_first_shot_s + e.first_shot_to_damage_s == pytest.approx(e.time_to_damage_s, abs=1e-3)


def test_contact_speed_marks_peek():
    # A 在 tick 80 从墙后走出来（这一 tick 位移 200），C 一直站着：A 主动拉出，C 架点
    mm = duel(c_yaw={t: 0.0 for t in range(1, 88)})
    e = mm.moments[0].engagement
    assert e.contact_tick == 80
    assert e.player_speed_at_contact > 120 and e.opponent_speed_at_contact < 40
    assert e.contact_type == "peek" and "peeked_held_angle" in mm.moments[0].tags


def test_first_seen_stops_at_floor_is_reported():
    # 回溯停在调用方给的下限（如上一次击杀）时，limit 为 floor，而不是"超过回溯时长"
    m = make_match(ticks(a_pos={t: 0.0 for t in range(1, 121)}))
    fs = sight(m).first_seen(A, C, 95, floor=60)
    assert fs.seen_at_limit and fs.limit == "floor"
    fs = sight(m).first_seen(A, C, 95, floor=1)
    assert fs.seen_at_limit and fs.limit == "floor"  # 回溯 3 秒超过了回合开始，下限仍是 floor
    long = make_match(make_ticks(last=400, overrides={(C, t): {"X": 1000.0} for t in range(1, 401)}))
    fs = sight(long).first_seen(A, C, 390, floor=1)
    assert fs.seen_at_limit and fs.limit == "lookback"


def test_grenade_damage_does_not_count_for_time_to_damage():
    m = make_match(ticks(a_yaw={80: 10.0}), rounds=[make_round(freeze_end=10, end=110, last=120)])
    m.kills = build_kills(pd.DataFrame([death(100, A, C)]), m)
    # 85 先被 A 的燃烧瓶烧到 1 点血，95 才第一次被枪打中
    m.damages = build_damages(pd.DataFrame([hurt(85, A, C, 1, 99, weapon="inferno"), hurt(95, A, C, 30, 69),
                                            hurt(100, A, C, 120, 0)]), m)
    fires = [fire(90, A), fire(95, A), fire(100, A)]
    m.shots = build_shots(pd.DataFrame(fires), pd.DataFrame([bullet(f["tick"], A) for f in fires]), m)
    e = find_moments(m, A, CFG, sight=Sight(m, CFG, checker=WallChecker(500.0, -50.0))).moments[0].engagement
    assert e.time_to_damage_s == pytest.approx(15 / 64, abs=1e-3)  # 80 看见 → 95 第一次枪械伤害
