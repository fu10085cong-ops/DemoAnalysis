"""视野计算与 Leetify 口径的枪法统计。

场景：x = 500 处有一堵墙（覆盖 y < -50）。C 站在 (1000, 0)。A 原本躲在 (0, -200)（被墙挡住），
tick 80 走到 (0, 0)（能看到 C）。A 朝 +X（yaw 0）。
"""

import copy
import math

import pandas as pd
import pytest

from cs2coach.config import ConfigError, load_config
from cs2coach.features.moments import find_moments
from cs2coach.features.visibility import PlayerState, Sight, angle_between
from cs2coach.parsing.match import build_blinds, build_bomb, build_damages, build_grenades, build_kills, build_shots
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


def test_only_head_side_or_shoulder_visible():
    class ExposedSide:
        def is_visible(self, _eye, point):
            return (round(point[2]), round(abs(point[1]))) in {(44, 6), (38, 16)}

    viewer = PlayerState(0, 0, 0, 0, 0, 0, True, 2)
    target = PlayerState(100, 0, 0, 0, 180, 1, True, 3)
    m = make_match(ticks())
    current = Sight(m, CFG, checker=ExposedSide())
    old = copy.deepcopy(CFG)
    old["visibility"]["body_points"] = old["visibility"]["body_points"][:6]
    assert not Sight(m, old, checker=ExposedSide()).sees(viewer, target)
    assert current.sees(viewer, target)


def test_head_inside_screen_while_chest_outside():
    class Clear:
        def is_visible(self, _eye, _point):
            return True

    cfg = copy.deepcopy(CFG)
    cfg["visibility"]["fov_vertical_half"] = 15.0
    s = Sight(make_match(ticks()), cfg, checker=Clear())
    viewer = PlayerState(0, 0, 0, 0, 0, 0, True, 2)
    target = PlayerState(50, 0, 0, 0, 180, 0, True, 3)
    points = s.body_points(target, s.eye(viewer))
    assert not s.in_view(viewer, s.eye(viewer), points[1])
    assert s.in_view(viewer, s.eye(viewer), points[6])
    assert s.sees(viewer, target)


@pytest.mark.parametrize("points", ["[[1, 2]]", "[[1, 2, 3, 4]]", "[[-1, 2, 0]]",
                                    '[[true, 2, 0]]', '[[1, "x", 0]]'])
def test_invalid_body_points_config(tmp_path, points):
    path = tmp_path / "invalid.toml"
    path.write_text(f"[visibility]\nbody_points = {points}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="visibility.body_points"):
        load_config(path)


@pytest.mark.parametrize("heights", ["[40]", "[-1, 28]", '[40, "bad"]'])
def test_invalid_chest_height_config(tmp_path, heights):
    path = tmp_path / "invalid.toml"
    path.write_text(f"[visibility]\nchest_height = {heights}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="visibility.chest_height"):
        load_config(path)


def _visibility_death(a_pos=None, a_yaw=None, own_damage_tick=None):
    """目标玩家 A 阵亡；可控制伤害锚点和逐 tick 视野。"""
    m = make_match(ticks(a_pos=a_pos, a_yaw=a_yaw),
                   rounds=[make_round(freeze_end=10, end=110, last=120)])
    m.kills = build_kills(pd.DataFrame([death(100, C, A)]), m)
    damages = [hurt(100, C, A, 100, 0)]
    if own_damage_tick is not None:
        damages.insert(0, hurt(own_damage_tick, A, C, 23, 77))
    m.damages = build_damages(pd.DataFrame(damages), m)
    fires = [fire(own_damage_tick if own_damage_tick is not None else 90, A), fire(100, C)]
    m.shots = build_shots(pd.DataFrame(fires),
                          pd.DataFrame([bullet(f["tick"], int(f["user_steamid"])) for f in fires]), m)
    m.blinds = build_blinds(pd.DataFrame(), pd.DataFrame(), m)
    m.bomb = build_bomb({}, m)
    mm = find_moments(m, A, CFG, sight=sight(m))
    return next(moment for moment in mm.moments if moment.id == "R1-death")


def test_first_hit_unseen_then_visible_before_death():
    blocked = {tick: -200.0 for tick in range(91, 96)}
    moment = _visibility_death(a_pos=blocked, own_damage_tick=95)
    e = moment.engagement
    assert e.visibility_note == "first_hit_unseen"
    assert e.time_to_damage_s is e.seen_to_first_shot_s is e.first_shot_to_damage_s is None
    assert e.crosshair_placement_deg is None
    assert "died_without_seeing" not in moment.tags
    from cs2coach.report.facts import moment_facts
    assert "你第一次打中对手时看不到他（可能是穿透或掩体边缘），之后看见了。" in moment_facts(moment, {})


def test_first_hit_unseen_takes_priority_over_gap_tolerance():
    blocked = {tick: -200.0 for tick in (94, 95)}
    moment = _visibility_death(a_pos=blocked, own_damage_tick=95)
    assert moment.engagement.player_first_seen_tick is not None  # gap 仍找到更早的可见起点
    assert moment.engagement.visibility_note == "first_hit_unseen"
    assert moment.engagement.time_to_damage_s is None


def test_lost_sight_records_last_seen_time():
    blocked = {tick: -200.0 for tick in range(95, 101)}
    moment = _visibility_death(a_pos=blocked)
    e = moment.engagement
    assert e.visibility_note == "lost_sight"
    assert e.last_seen_before_s == pytest.approx(6 / 64, abs=.001)
    assert e.time_to_damage_s is e.seen_to_first_shot_s is e.first_shot_to_damage_s is None
    assert e.crosshair_placement_deg is None
    assert "died_without_seeing" not in moment.tags
    from cs2coach.report.facts import moment_facts
    assert "你在 0.1 秒前看见过对手，之后视线中断。" in moment_facts(moment, {})


@pytest.mark.parametrize("off_screen", [True, False])
def test_never_seen_distinguishes_off_screen_from_occlusion(off_screen):
    yaw = {tick: 180.0 for tick in range(1, 121)} if off_screen else None
    pos = None if off_screen else {tick: -200.0 for tick in range(1, 121)}
    moment = _visibility_death(a_pos=pos, a_yaw=yaw)
    e = moment.engagement
    assert e.visibility_note == "not_seen" and e.opponent_off_screen is off_screen
    assert "died_without_seeing" in moment.tags
    from cs2coach.report.facts import moment_facts
    expected = ("对手在你的视野之外（侧面或背后），你被击杀前没有看见他。" if off_screen else
                "按视野计算，你被击杀前没有看见对手。")
    assert expected in moment_facts(moment, {})


def test_smoke_at_first_seen_marks_unreliable():
    m = make_match(ticks())
    smoke = pd.DataFrame([{"tick": 10, "user_steamid": sid(B), "entityid": 1, "x": 500.0, "y": 0.0, "z": 0.0}])
    s = sight(m, {"smokegrenade_detonate": smoke})
    assert s.first_seen(A, C, 95, floor=10).smoke
    # 烟刚展开（不到 smoke_active_seconds 的下限）时不算
    young = pd.DataFrame([{"tick": 70, "user_steamid": sid(B), "entityid": 1, "x": 500.0, "y": 0.0, "z": 0.0}])
    assert not sight(make_match(ticks()), {"smokegrenade_detonate": young}).first_seen(A, C, 95, floor=10).smoke


def test_smoke_uses_chest_height_independent_of_body_point_order():
    smoke = pd.DataFrame([{"tick": 10, "user_steamid": sid(B), "entityid": 1,
                           "x": 500.0, "y": 0.0, "z": 0.0}])
    m = make_match(ticks())
    m.grenades = build_grenades({"smokegrenade_detonate": smoke}, m)
    cfg = copy.deepcopy(CFG)
    cfg["visibility"]["body_points"].reverse()
    s = Sight(m, cfg, checker=WallChecker(500.0, -50.0))
    viewer, target = m.state_at(A, 95), m.state_at(C, 95)
    assert s.smoke_between(viewer, target, 95)


def test_angle_between_view_vectors():
    m = make_match(ticks(a_yaw={80: 10.0}))
    s = sight(m)
    assert angle_between(s.view_vector(A, 80), s.view_vector(A, 95)) == pytest.approx(10.0, abs=0.01)
    assert angle_between(None, s.view_vector(A, 95)) is None


# ---------- 接入交火与整场统计 ----------


def duel(cfg=CFG, checker=None, c_yaw=None, blinds=None, damages=None, smokes=None):
    m = make_match(ticks(a_yaw={80: 10.0}, c_yaw=c_yaw), rounds=[make_round(freeze_end=10, end=110, last=120)])
    m.kills = build_kills(pd.DataFrame([death(100, A, C)]), m)
    m.damages = build_damages(pd.DataFrame(damages if damages is not None else
                                         [hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0)]), m)
    fires = [fire(90, A), fire(95, A), fire(100, A)]
    m.shots = build_shots(pd.DataFrame(fires), pd.DataFrame([bullet(f["tick"], A) for f in fires]), m)
    if blinds is not None:
        m.blinds = build_blinds(pd.DataFrame(blinds), pd.DataFrame(), m)
    if smokes is not None:
        m.grenades = build_grenades(smokes, m)
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
    first_seen = next(s for s in e.shots if s.seen_opponent is True)
    assert e.first_seen_shot_stopped == first_seen.stopped
    assert e.first_seen_shot_hit == first_seen.hit
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


@pytest.mark.parametrize("previous_scoped,expected", [(True, False), (False, True)])
def test_aim_stats_uses_scope_before_aug_shot(previous_scoped, expected):
    from cs2coach.features.aim_stats import aim_stats

    cfg = copy.deepcopy(CFG)
    cfg["aim_stats"]["min_samples"] = 1
    m = make_match(ticks(extra={(A, 89): {"is_scoped": previous_scoped},
                               (A, 90): {"is_scoped": False, "X": 0.9375}}))
    m.damages = build_damages(pd.DataFrame([hurt(90, A, C, 30, 70, weapon="aug")]), m)
    m.shots = build_shots(pd.DataFrame([fire(90, A, "weapon_aug")]), pd.DataFrame([bullet(90, A)]), m)
    st = aim_stats(m, A, cfg, m.shots, sight(m), [])
    assert st.counter_strafing_shots == 1
    assert st.counter_strafing_pct == (100.0 if expected else 0.0)


def test_aim_stats_reads_scoped_awp_speed_without_changing_rifle_scope():
    from unittest.mock import Mock

    from cs2coach.features.aim_stats import aim_stats

    cfg = copy.deepcopy(CFG)
    scoped_speeds = Mock(wraps=cfg["counter_strafe"]["max_speed_scoped"])
    cfg["counter_strafe"]["max_speed_scoped"] = scoped_speeds
    m = make_match(ticks(extra={(A, 89): {"is_scoped": True},
                               (A, 90): {"is_scoped": False, "X": 0.9375}}))
    m.damages = build_damages(pd.DataFrame([hurt(90, A, C, 30, 70, weapon="awp")]), m)
    m.shots = build_shots(pd.DataFrame([fire(90, A, "weapon_awp")]), pd.DataFrame([bullet(90, A)]), m)
    st = aim_stats(m, A, cfg, m.shots, sight(m), [])
    scoped_speeds.get.assert_called_once_with("awp")
    assert st.counter_strafing_shots == 0  # 整场 Counter-Strafing 仍只统计步枪。


def flash(victim=A, tick=16, duration=2.0):
    return {"tick": tick, "user_steamid": sid(victim), "attacker_steamid": sid(B), "blind_duration": duration}


def test_seen_while_blind_excludes_own_metrics_but_keeps_geometric_tick():
    mm = duel(blinds=[flash()], c_yaw={t: 0.0 for t in range(1, 88)})
    e = mm.moments[0].engagement
    assert e.player_first_seen_tick == 80 and e.opponent_first_seen_tick == 88
    assert e.player_seen_while_blind is True and e.opponent_seen_while_blind is False
    assert e.visibility_note == "blind"
    assert e.time_to_damage_s is e.crosshair_placement_deg is None
    assert e.seen_to_first_shot_s is e.first_shot_to_damage_s is None
    assert e.seen_first_lead_s is None
    assert not {"saw_opponent_first", "opponent_saw_first"}.intersection(mm.moments[0].tags)


@pytest.mark.parametrize("record", [flash(tick=15), flash(tick=80, duration=0.5), flash(tick=81)])
def test_fading_short_and_future_blinds_do_not_exclude_visibility(record):
    e = duel(blinds=[record]).moments[0].engagement
    assert e.player_seen_while_blind is False and e.visibility_note is None
    assert e.time_to_damage_s == pytest.approx(15 / 64, abs=1e-3)


def test_opponent_blind_only_excludes_opponent_ttd_and_seen_lead():
    damages = [hurt(94, C, A, 30, 70), hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0)]
    mm = duel(blinds=[flash(C)], damages=damages)
    e = mm.moments[0].engagement
    assert e.player_seen_while_blind is False and e.opponent_seen_while_blind is True
    assert e.visibility_note is None and e.opponent_time_to_damage_s is None
    assert e.time_to_damage_s == pytest.approx(15 / 64, abs=1e-3)
    assert e.crosshair_placement_deg is not None and e.seen_first_lead_s is None
    from cs2coach.report.facts import moment_facts

    assert "对手第一次看见你时正被闪白，不计算对手的反应时间。" in moment_facts(mm.moments[0], {})


def test_smoke_has_priority_over_blind_and_keeps_audit_tick():
    smoke = pd.DataFrame([{"tick": 10, "user_steamid": sid(B), "entityid": 1,
                           "x": 500.0, "y": 0.0, "z": 0.0}])
    e = duel(blinds=[flash()], smokes={"smokegrenade_detonate": smoke}).moments[0].engagement
    assert e.visibility_note == "smoke" and e.player_seen_while_blind is True
    assert e.player_first_seen_tick == 80 and e.time_to_damage_s is None


def test_aim_stats_excludes_blind_encounters_and_keeps_other_statistics():
    cfg = copy.deepcopy(CFG)
    cfg["aim_stats"]["min_samples"] = 1
    st = duel(cfg, blinds=[flash()]).aim_stats
    assert st.time_to_damage_samples == st.crosshair_placement_samples == 0
    assert st.time_to_damage_ms is st.crosshair_placement_deg is None
    assert st.spotted_shots == 3 and st.hits == 2


def test_blind_flags_are_unknown_without_geometric_first_seen():
    e = duel(c_yaw={t: 0.0 for t in range(1, 121)}, blinds=[flash(C)]).moments[0].engagement
    assert e.opponent_first_seen_tick is None and e.opponent_seen_while_blind is None


def test_blind_filter_uses_configured_remaining_threshold():
    cfg = copy.deepcopy(CFG)
    cfg["visibility"]["blind_min_remaining_seconds"] = 1.1
    assert duel(cfg, blinds=[flash()]).moments[0].engagement.player_seen_while_blind is False


# ---------- 031：准星初始偏差、空中排除 ----------


def _duel_with(extra):
    m = make_match(ticks(a_yaw={80: 10.0}, extra=extra), rounds=[make_round(freeze_end=10, end=110, last=120)])
    m.kills = build_kills(pd.DataFrame([death(100, A, C)]), m)
    m.damages = build_damages(pd.DataFrame([hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0)]), m)
    fires = [fire(90, A), fire(95, A), fire(100, A)]
    m.shots = build_shots(pd.DataFrame(fires), pd.DataFrame([bullet(f["tick"], A) for f in fires]), m)
    return find_moments(m, A, CFG, sight=Sight(m, CFG, checker=WallChecker(wall_x=500.0, wall_y_max=-50.0))).moments[0].engagement


def test_crosshair_initial_offset_horizontal():
    # 80 看见：A 在 (0,0) 朝 10°，C 在 1000 单位外正前方 → 水平偏 10°，偏差 = 25.4 米 × sin 10°
    e = _duel_with({})
    assert e.crosshair_initial_deg == pytest.approx(10.0, abs=0.01)
    assert e.crosshair_initial_yaw_deg == pytest.approx(10.0, abs=0.01)
    assert e.crosshair_initial_pitch_deg == pytest.approx(0.0, abs=0.01)
    assert e.crosshair_initial_offset_m == pytest.approx(1000 / 39.37 * math.sin(math.radians(10)), abs=0.01)
    assert e.airborne_near_seen is False


def test_crosshair_initial_offset_vertical_sign():
    # 准星低于头 5°（pitch 正数表示向下看）
    e = _duel_with({(A, 80): {"Y": 0.0, "yaw": 0.0, "pitch": 5.0}})
    assert e.crosshair_initial_yaw_deg == pytest.approx(0.0, abs=0.01)
    assert e.crosshair_initial_pitch_deg == pytest.approx(5.0, abs=0.01)
    assert e.crosshair_initial_offset_m == pytest.approx(1000 / 39.37 * math.sin(math.radians(5)), abs=0.01)


@pytest.mark.parametrize("tick,expected", [(70, True), (95, True), (60, False), (96, False)])
def test_airborne_near_seen_window(tick, expected):
    # 区间 [80 − 0.25 秒（16 tick）, 95 第一次伤害]
    y = -200.0 if tick < 80 else 0.0
    e = _duel_with({(A, tick): {"Y": y, "yaw": 10.0 if tick == 80 else 0.0, "is_airborne": True}})
    assert e.airborne_near_seen is expected
