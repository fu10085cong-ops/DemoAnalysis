"""决定性瞬间与指标。用合成数据构造一个回合：

A、B 是 T，C、D 是 CT，目标玩家 A。回合 1：start 1，冻结期结束 10，胜负判定 300。
位置：A、B 在原点附近；C 在 A 正前方（X=1000），D 在 A 左侧（Y=1000）。各自朝对方开枪。
- tick 85 C 先开枪；A 在 90（移动中）、95、100（停稳）开枪，95 和 100 命中，100 击杀 C
- tick 150 D 击杀 B（A 就在旁边，没补上）
- tick 190 D 的闪光致盲 A 2 秒；tick 200 D 击杀 A（A 没开枪，身上还有一颗烟）
"""

import numpy as np
import pandas as pd
import pytest

from cs2coach.config import load_config
from cs2coach.features.moments import bomb_planted
from cs2coach.features.moments import find_moments as _find_moments
from cs2coach.features.moments import _Params, find_missed_trades, trade_info
from cs2coach.features.visibility import Sight
from cs2coach.parsing.match import (
    add_death_inventory,
    build_blinds,
    build_bomb,
    build_damages,
    build_kills,
    build_shots,
    effective_damage,
)
from tests.helpers.synthetic import A, B, C, D, AlwaysVisible, make_match, make_round, make_ticks, sid

CFG = load_config()


def find_moments(m, steamid, cfg):
    """测试中不加载真实地图数据：注入没有遮挡的视线检测。"""
    return _find_moments(m, steamid, cfg, sight=Sight(m, cfg, checker=AlwaysVisible()))


def death(tick, attacker, victim, weapon="ak47", assister=None, **flags):
    return {"tick": tick, "attacker_steamid": sid(attacker), "user_steamid": sid(victim),
            "assister_steamid": sid(assister), "weapon": weapon, "headshot": flags.get("headshot", False),
            "thrusmoke": False, "attackerblind": False, "noscope": False,
            "assistedflash": flags.get("assistedflash", False), "penetrated": 0, "hitgroup": "chest", "distance": 10.0}


def hurt(tick, attacker, victim, dmg, health, weapon="ak47"):
    return {"tick": tick, "attacker_steamid": sid(attacker), "user_steamid": sid(victim), "weapon": weapon,
            "dmg_health": dmg, "dmg_armor": 0, "health": health, "hitgroup": "chest"}


def fire(tick, steamid, weapon="weapon_ak47"):
    return {"tick": tick, "user_steamid": sid(steamid), "weapon": weapon}


# 每名玩家开枪的方向（yaw）：A 朝 C（正前方），C 朝 A，D 朝 A
AIM_YAW = {A: 0.0, B: 0.0, C: 180.0, D: -90.0}


def bullet(tick, steamid, yaw=None):
    return {"tick": tick, "user_steamid": sid(steamid), "angles_x": 0.0,
            "angles_y": AIM_YAW[steamid] if yaw is None else yaw, "inaccuracy": 0.01, "spread": 0.0, "recoil_index": 0.0}


def scenario_ticks(extra: dict | None = None):
    o = {(C, t): {"X": 1000.0} for t in range(1, 401)}
    o.update({(D, t): {"Y": 1000.0} for t in range(1, 401)})
    o[(D, 155)] = {"Y": 1000.0, "spotted_by_mask": 1}  # A（位 0）在雷达上发现了 D
    o[(A, 89)] = {"X": 0.0}
    o[(A, 90)] = {"X": 3.5}  # 90 这一 tick 速度 224 u/s：移动中开枪
    for t in range(91, 401):
        o[(A, t)] = {"X": 3.5}
    for s_, dead_from in ((C, 100), (B, 150), (A, 200)):
        for t in range(dead_from, 401):
            o[(s_, t)] = {**o.get((s_, t), {}), "is_alive": False, "health": 0}
    for t in range(95, 100):
        o[(C, t)] = {**o.get((C, t), {}), "health": 70}
    o.update(extra or {})
    return make_ticks(last=400, overrides=o)


def scenario(kills=None, damages=None, fires=None, bullets=None, blinds=None, extra_ticks=None, inventory=None):
    m = make_match(scenario_ticks(extra_ticks), rounds=[make_round(freeze_end=10, end=300, last=400)])
    kills = kills if kills is not None else [death(100, A, C), death(150, D, B), death(200, D, A)]
    damages = damages if damages is not None else [hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0),
                                                   hurt(150, D, B, 100, 0), hurt(200, D, A, 100, 0)]
    fires = fires if fires is not None else [fire(85, C), fire(90, A), fire(95, A), fire(100, A),
                                             fire(150, D), fire(198, D), fire(200, D)]
    bullets = bullets if bullets is not None else [bullet(f["tick"], int(f["user_steamid"])) for f in fires]
    m.kills = build_kills(pd.DataFrame(kills), m)
    m.damages = build_damages(pd.DataFrame(damages), m)
    m.shots = build_shots(pd.DataFrame(fires), pd.DataFrame(bullets), m)
    pb = blinds if blinds is not None else [{"tick": 190, "user_steamid": sid(A), "attacker_steamid": sid(D), "blind_duration": 2.0}]
    m.blinds = build_blinds(pd.DataFrame(pb), pd.DataFrame(), m)
    m.bomb = build_bomb({}, m)
    snap = pd.DataFrame([{"steamid": s_, "tick": int(t) - 1, "inventory": (inventory or {}).get(s_, ["knife_t", "AK-47", "Smoke Grenade"])}
                         for t in m.kills["tick"] for s_ in (A, B, C, D)]).drop_duplicates(["steamid", "tick"])
    m.kills = add_death_inventory(m.kills, snap, m)
    return m


def moment(mm, mid):
    return next(x for x in mm.moments if x.id == mid)


@pytest.mark.parametrize("delay_seconds,traded", [(4, True), (6, False)])
def test_trade_and_missed_trade_use_five_second_window(delay_seconds, traded):
    # A、B 同队，C 击杀 B；A 在 4/6 秒后击杀 C。C 在阵亡后曾被 A 的雷达发现。
    revenge_tick = 100 + delay_seconds * 64
    ticks = make_ticks(last=600, overrides={(C, 100): {"spotted_by_mask": 1}})
    match = make_match(ticks, rounds=[make_round(end=550, last=600)])
    match.kills = build_kills(pd.DataFrame([death(100, C, B), death(revenge_tick, A, C)]), match)
    params = _Params(match, CFG)
    assert params.trade == 5 * 64
    assert trade_info(match, params, B, C, 100, "death").traded is traded
    assert trade_info(match, params, A, C, revenge_tick, "kill").is_trade_kill is traded
    assert bool(find_missed_trades(match, params, A)) is not traded


def test_one_trade_kill_can_avenge_two_teammates():
    ticks = make_ticks(last=400)
    ticks.loc[ticks["steamid"] == C, "team_num"] = 2
    match = make_match(ticks, rounds=[make_round(end=350, last=400)])
    match.kills = build_kills(pd.DataFrame([death(100, D, A), death(150, D, B),
                                            death(200, C, D)]), match)
    trade = trade_info(match, _Params(match, CFG), C, D, 200, "kill")
    assert trade.is_trade_kill and trade.avenged_teammate_count == 2
    assert trade.avenged_teammate == B


def test_shot_seen_opponent_uses_previous_tick_and_missing_sight():
    extra = {(A, 89): {"yaw": 180.0, "X": 0.0},
             (A, 94): {"yaw": 180.0, "X": 3.5},
             (A, 99): {"yaw": 0.0, "X": 3.5}}
    match = scenario(extra_ticks=extra)
    sight = Sight(match, CFG, checker=AlwaysVisible())
    shots = moment(_find_moments(match, A, CFG, sight=sight), "R1-kill1").engagement.shots
    assert [shot.seen_opponent for shot in shots] == [False, False, True]
    sight.checker = None
    shots = moment(_find_moments(match, A, CFG, sight=sight), "R1-kill1").engagement.shots
    assert all(shot.seen_opponent is None for shot in shots)


def test_shot_visibility_checks_engagement_opponent_not_any_enemy():
    # 90 tick 的子弹朝 C 飞，前一 tick A 看着 D，不能把 D 的可见性算给 C。
    match = scenario(extra_ticks={(A, 89): {"yaw": 90.0}})
    sight = Sight(match, CFG, checker=AlwaysVisible())
    assert sight.any_enemy_seen(A, 89)
    shot = moment(_find_moments(match, A, CFG, sight=sight), "R1-kill1").engagement.shots[0]
    assert shot.tick == 90 and shot.seen_opponent is False


# ---------- 交火 ----------


def test_engagement_window_and_shots():
    mm = find_moments(scenario(), A, CFG)
    k = moment(mm, "R1-kill1")
    e = k.engagement
    assert (e.start_tick, e.end_tick) == (85, 100)  # 从 C 在 85 的开枪串起来
    assert e.shots_fired == 3 and e.shots_hit == 2 and e.first_shot_hit is False
    assert e.damage_dealt == 100  # 实际扣血 30 + 70，不是原始伤害 30 + 120
    assert e.damage_taken == 0
    assert e.player_first_shot_tick == 90 and e.opponent_first_shot_tick == 85
    assert e.first_shot_lead_s == pytest.approx(-5 / 64, abs=1e-3)
    # 急停：90 那一枪 224 u/s（AK 最高 215）没停稳，95、100 停稳
    assert [s.stopped for s in e.shots] == [False, True, True]
    assert e.shots[0].speed_ratio == pytest.approx(224 / 215, abs=0.01)
    assert (e.counter_strafe_shots, e.counter_strafe_stopped, e.first_shot_stopped) == (3, 2, False)
    assert "opponent_shot_first" in k.tags and "opening_kill" in k.tags


def test_engagement_ignores_activity_before_gap():
    # tick 20 的开枪与后面隔了 1 秒以上，不属于这次交火
    fires = [fire(20, A), fire(85, C), fire(90, A), fire(95, A), fire(100, A), fire(150, D), fire(200, D)]
    e = moment(find_moments(scenario(fires=fires), A, CFG), "R1-kill1").engagement
    assert e.start_tick == 85 and e.shots_fired == 3


def test_crouched_shots_excluded_from_counter_strafe():
    extra = {(A, 95): {"X": 3.5, "duck_amount": 1.0}}
    e = moment(find_moments(scenario(extra_ticks=extra), A, CFG), "R1-kill1").engagement
    assert e.shots[1].crouched and e.counter_strafe_shots == 2


def test_unknown_weapon_speed_is_none():
    fires = [fire(85, C), fire(90, A, "weapon_some_new_gun"), fire(95, A), fire(100, A), fire(150, D), fire(200, D)]
    e = moment(find_moments(scenario(fires=fires), A, CFG), "R1-kill1").engagement
    assert e.shots_fired == 2  # 认不出的武器不算枪械


# ---------- 死亡 ----------


def test_death_moment():
    mm = find_moments(scenario(), A, CFG)
    d = moment(mm, "R1-death")
    assert d.opponent == D and d.engagement.shots_fired == 0
    assert d.death.unused_grenades == ["smokegrenade"]
    assert d.death.blinded and d.death.blinded_by == D and d.death.blinded_by_teammate is False
    assert d.death.blind_remaining_s == pytest.approx((190 + 128 - 200) / 64, abs=0.01)
    assert d.trade.traded is False and d.trade.nearest_teammate_distance is None  # B 已经阵亡
    assert d.context.alive_own == 1 and d.context.alive_enemy == 1
    for tag in ("no_shots_fired", "died_blind", "unused_utility", "not_traded", "clutch_1v1"):
        assert tag in d.tags


def test_short_blind_does_not_count():
    blinds = [{"tick": 190, "user_steamid": sid(A), "attacker_steamid": sid(D), "blind_duration": 0.5}]
    d = moment(find_moments(scenario(blinds=blinds), A, CFG), "R1-death")
    assert not d.death.blinded


def test_traded_death():
    kills = [death(100, A, C), death(200, D, A), death(250, B, D)]
    damages = [hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0), hurt(200, D, A, 100, 0), hurt(250, B, D, 100, 0)]
    extra = {(B, t): {"is_alive": True, "health": 100, "X": 200.0} for t in range(150, 401)}
    d = moment(find_moments(scenario(kills=kills, damages=damages, extra_ticks=extra), A, CFG), "R1-death")
    assert d.trade.traded and d.trade.traded_by == B
    assert d.trade.trade_delay_s == pytest.approx(50 / 64, abs=1e-3)
    assert d.trade.nearest_teammate_distance == pytest.approx(196.5, abs=0.1)  # A 在 X=3.5，B 在 X=200
    assert "traded" in d.tags


def test_trade_kill():
    kills = [death(60, C, B), death(100, A, C), death(200, D, A)]
    damages = [hurt(60, C, B, 100, 0), hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0), hurt(200, D, A, 100, 0)]
    k = moment(find_moments(scenario(kills=kills, damages=damages), A, CFG), "R1-kill1")
    assert k.trade.is_trade_kill and k.trade.avenged_teammate == B
    assert "trade_kill" in k.tags and "opening_kill" not in k.tags


def test_missed_trade():
    mm = find_moments(scenario(), A, CFG)
    assert len(mm.missed_trades) == 1
    t = mm.missed_trades[0]
    assert (t.tick, t.teammate, t.killer, t.killer_traded_by_other) == (150, B, D, False)
    assert (t.side, t.alive_own, t.alive_enemy, t.round_time_s, t.bomb_planted) == (
        "T", 1, 1, pytest.approx((150 - 10) / 64, abs=0.01), False)


def test_missed_trade_bomb_context_before_and_after_plant():
    before = scenario()
    before.bomb = pd.DataFrame([{"tick": 160, "round": 1, "event": "planted"}])
    assert find_moments(before, A, CFG).missed_trades[0].bomb_planted is False
    after = scenario()
    after.bomb = pd.DataFrame([{"tick": 140, "round": 1, "event": "planted"}])
    assert find_moments(after, A, CFG).missed_trades[0].bomb_planted is True


def test_missed_trade_requires_seeing_the_killer():
    extra = {(D, 155): {"Y": 1000.0, "spotted_by_mask": 0}}
    assert find_moments(scenario(extra_ticks=extra), A, CFG).missed_trades == []


def test_shots_at_other_enemies_are_not_in_engagement():
    # A 在 92 朝 D 开了一枪（yaw 90°），不属于与 C 的交火
    fires = [fire(85, C), fire(90, A), fire(92, A), fire(95, A), fire(100, A), fire(150, D), fire(200, D)]
    bullets = [bullet(f["tick"], int(f["user_steamid"])) for f in fires]
    bullets[2] = bullet(92, A, yaw=90.0)
    e = moment(find_moments(scenario(fires=fires, bullets=bullets), A, CFG), "R1-kill1").engagement
    assert [s.tick for s in e.shots] == [90, 95, 100]
    assert all(s.aim_error_deg is not None and s.aim_error_deg < 5 for s in e.shots)


def test_far_teammate_death_is_not_missed_trade():
    extra = {(B, t): {"X": 5000.0} for t in range(1, 401)}
    assert find_moments(scenario(extra_ticks=extra), A, CFG).missed_trades == []


# ---------- 助攻 ----------


def test_key_assists():
    kills = [death(100, A, C), death(150, B, D, assister=A, assistedflash=True), death(200, D, A)]
    m = scenario(kills=kills, damages=[hurt(100, A, C, 100, 0)])
    mm = find_moments(m, A, CFG)
    a = moment(mm, "R1-assist1")
    assert a.assist.flash_assist and a.assist.killer == B and "flash_assist" in a.tags

    # 伤害助攻：低于 key_assist_min_damage 不算关键助攻
    kills = [death(100, A, C), death(150, B, D, assister=A), death(200, D, A)]
    low = scenario(kills=kills, damages=[hurt(100, A, C, 100, 0), hurt(140, A, D, 45, 55)])
    assert not [x for x in find_moments(low, A, CFG).moments if x.kind == "assist"]
    high = scenario(kills=kills, damages=[hurt(100, A, C, 100, 0), hurt(140, A, D, 80, 20)])
    a = moment(find_moments(high, A, CFG), "R1-assist1")
    assert a.assist.damage_to_victim == 80 and "damage_assist" in a.tags


# ---------- 排除、编号、跳转 ----------


def test_world_death():
    # 摔死 / 炸死：没有击杀者，也就没有交火和补枪
    kills = [death(100, A, C), death(200, None, A, weapon="world")]
    damages = [hurt(95, A, C, 30, 70), hurt(100, A, C, 120, 0), hurt(200, None, A, 100, 0, weapon="")]
    d = moment(find_moments(scenario(kills=kills, damages=damages), A, CFG), "R1-death")
    assert d.opponent is None and d.engagement is None and d.trade is None
    assert "world_damage" in d.tags


def test_excluded_records():
    extra = {(A, t): {"controlling_bot": True, "X": 3.5} for t in range(95, 101)}
    m = scenario(extra_ticks=extra)
    mm = find_moments(m, A, CFG)
    assert [x.id for x in mm.moments] == ["R1-death"]
    assert len(mm.excluded) == 1 and "bot" in mm.excluded[0].reason


def test_ids_and_jump():
    mm = find_moments(scenario(), A, CFG)
    assert [x.id for x in mm.moments] == ["R1-kill1", "R1-death"]
    k = moment(mm, "R1-kill1")
    assert k.jump_tick == max(1, 85 - 5 * 64) and k.jump_command == f"demo_gototick {k.jump_tick}"


def test_bomb_planted():
    m = make_match()
    m.bomb = build_bomb({"bomb_planted": pd.DataFrame([{"tick": 50, "user_steamid": sid(A)}]),
                         "bomb_defused": pd.DataFrame([{"tick": 90, "user_steamid": sid(C)}])}, m)
    assert not bomb_planted(m, 1, 50) and bomb_planted(m, 1, 51) and not bomb_planted(m, 1, 91)


# ---------- 实际扣血 ----------


def test_effective_damage():
    df = pd.DataFrame({
        "round": [1, 1, 1, 2], "victim": [C, C, D, C],
        "dmg_health": [30, 120, 400, 20], "health": [70, 0, 0, 80],
        "victim_prev_health": [100, 70, 100, np.nan],
    })
    # 第二枪原始伤害 120，但 C 只剩 70；AWP 打满血 400 只算 100；第二回合重新从满血算
    assert effective_damage(df) == [30, 70, 100, 20]


@pytest.mark.parametrize("mine,theirs,expected", [
    (200.0, 5.0, "peek"),        # 自己跑动进入视野，对手架点
    (5.0, 200.0, "held"),
    (200.0, 180.0, "both_moving"),
    (10.0, 0.0, "both_still"),
    (90.0, 0.0, "unclear"),      # 自己在减速 / 急停中，不下结论
    (None, 0.0, None),
])
def test_contact_type(mine, theirs, expected):
    from cs2coach.features.moments import contact_type

    assert contact_type(mine, theirs, 40.0, 120.0) == expected



def test_weapon_not_ready_after_awp_shot():
    # A 在 tick 180 开了一枪 AWP（打别的方向），tick 200 阵亡：只隔 20 tick，AWP 两枪最短间隔 1.45 秒，来不及再开枪
    fires = [fire(85, C), fire(90, A), fire(95, A), fire(100, A), fire(150, D), fire(180, A, "weapon_awp"), fire(200, D)]
    bullets = [bullet(f["tick"], int(f["user_steamid"])) for f in fires]
    bullets[5] = bullet(180, A, yaw=135.0)  # 没瞄向任何人
    d = moment(find_moments(scenario(fires=fires, bullets=bullets), A, CFG), "R1-death")
    assert d.death.weapon_ready is False and d.death.last_shot_weapon == "awp"
    assert d.death.last_shot_gap_s == pytest.approx(20 / 64, abs=1e-3) and d.death.min_shot_interval_s == 1.45
    assert "weapon_not_ready" in d.tags


def test_weapon_ready_when_last_shot_is_old_or_fast_weapon():
    # 上一枪是 AK（没有配置最短间隔）：不做判断
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    assert d.death.weapon_ready is None and "weapon_not_ready" not in d.tags


@pytest.mark.parametrize("previous_scoped", [True, False])
def test_awp_first_shot_reads_previous_tick_scope(previous_scoped):
    # 交火只在 tick 100 发生；AWP 开枪后退镜，仍须读到窗口外 tick 99 的状态。
    m = scenario(kills=[death(100, A, C, weapon="awp")],
                 damages=[hurt(100, A, C, 100, 0, weapon="awp")],
                 fires=[fire(100, A, "weapon_awp")],
                 extra_ticks={(A, 99): {"X": 3.5, "is_scoped": previous_scoped},
                              (A, 100): {"X": 4.5, "is_scoped": False, "duck_amount": 1.0,
                                         "is_airborne": True}})
    e = moment(find_moments(m, A, CFG), "R1-kill1").engagement
    shot = e.shots[0]
    speeds = CFG["counter_strafe"]
    expected = speeds["max_speed_scoped" if previous_scoped else "max_speed"]["awp"]
    assert e.start_tick == shot.tick == 100
    assert shot.scoped is previous_scoped and shot.max_speed == expected
    assert shot.speed == 64.0 and shot.crouched and shot.airborne
    assert shot.stopped is (64.0 / expected < speeds["accurate_speed_ratio"])


@pytest.mark.parametrize("thrower,role", [(A, "self"), (B, "teammate"), (C, "opponent"), (D, "enemy")])
def test_opponent_blind_at_kill_and_thrower_roles(thrower, role):
    m = scenario(blinds=[{"tick": 80, "user_steamid": sid(C), "attacker_steamid": sid(thrower),
                          "blind_duration": 1.5}])
    k = moment(find_moments(m, A, CFG), "R1-kill1")
    b = k.engagement.opponent_blind
    assert b.blinded_at_event and b.remaining_at_event_s == 1.19
    assert b.max_duration_s == 1.5 and b.thrower == thrower
    assert b.thrower_role == role and not b.thrower_ambiguous
    assert "opponent_blind" in k.tags


def test_blind_overlaps_window_but_has_ended_at_event():
    # 致盲从窗口前开始，在 tick 90 结束，仍与 [85, 100] 重叠。
    m = scenario(blinds=[{"tick": 10, "user_steamid": sid(C), "attacker_steamid": sid(A),
                          "blind_duration": 1.25}])
    b = moment(find_moments(m, A, CFG), "R1-kill1").engagement.opponent_blind
    assert not b.blinded_at_event and b.remaining_at_event_s is None
    assert b.max_duration_s == 1.25 and b.thrower_role == "self"


@pytest.mark.parametrize("tick,duration", [(80, 0.5), (20, 1.0), (21, 1.0), (101, 2.0)])
def test_blind_excludes_short_ended_and_future_records(tick, duration):
    # tick 21 的一秒致盲恰好在窗口起点 85 结束，不再生效。
    m = scenario(blinds=[{"tick": tick, "user_steamid": sid(C), "attacker_steamid": sid(B),
                          "blind_duration": duration}])
    k = moment(find_moments(m, A, CFG), "R1-kill1")
    assert k.engagement.opponent_blind is None and "opponent_blind" not in k.tags


def test_blind_selects_longest_remaining_and_tracks_max_total_duration():
    m = scenario(blinds=[{"tick": 40, "user_steamid": sid(C), "attacker_steamid": sid(A), "blind_duration": 2.0},
                          {"tick": 80, "user_steamid": sid(C), "attacker_steamid": sid(B), "blind_duration": 1.5}])
    b = moment(find_moments(m, A, CFG), "R1-kill1").engagement.opponent_blind
    assert b.thrower == B and b.remaining_at_event_s == 1.19 and b.max_duration_s == 2.0


def test_blind_selects_longest_duration_when_all_have_ended():
    m = scenario(blinds=[{"tick": 10, "user_steamid": sid(C), "attacker_steamid": sid(A), "blind_duration": 1.25},
                          {"tick": 25, "user_steamid": sid(C), "attacker_steamid": sid(B), "blind_duration": 1.0}])
    b = moment(find_moments(m, A, CFG), "R1-kill1").engagement.opponent_blind
    assert b.thrower == A and b.max_duration_s == 1.25 and not b.blinded_at_event


def test_blind_ambiguous_thrower_stays_unknown():
    m = scenario(blinds=[{"tick": 80, "user_steamid": sid(C), "attacker_steamid": sid(None), "blind_duration": 1.5}])
    m.blinds["thrower_ambiguous"] = True
    b = moment(find_moments(m, A, CFG), "R1-kill1").engagement.opponent_blind
    assert b.thrower is None and b.thrower_role is None and b.thrower_ambiguous


def test_player_blind_in_fight_keeps_death_blind_fields():
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    b = d.engagement.player_blind
    assert b.blinded_at_event and b.remaining_at_event_s == d.death.blind_remaining_s
    assert b.thrower == d.death.blinded_by == D and b.thrower_role == "opponent"
    assert "blind_in_fight" in d.tags and "died_blind" in d.tags


def test_thrown_grenades_keep_order_duplicates_and_exclude_other_rounds_and_bots():
    from cs2coach.features.moments import _Params, death_state

    # 第一回合在 120 结束；第二回合 tick 200 阵亡。
    m = scenario(fires=[fire(50, A, "weapon_smokegrenade"), fire(130, A, "weapon_flashbang"),
                         fire(140, B, "weapon_hegrenade"), fire(150, A, "weapon_flashbang"),
                         fire(160, A, "weapon_molotov"), fire(170, A, "weapon_smokegrenade"),
                         fire(180, A), fire(200, A, "weapon_hegrenade"), fire(210, A, "weapon_decoy")],
                 extra_ticks={(A, 160): {"X": 3.5, "controlling_bot": True}})
    rounds = [make_round(1, end=100, last=120), make_round(2, start=121, freeze_end=125, end=300, last=400)]
    m.rounds = rounds
    from cs2coach.parsing.match import assign_rounds

    m.shots["round"] = assign_rounds(m.shots["tick"], rounds)
    row = m.kills[m.kills["victim"] == A].iloc[0].copy()
    row["round"] = 2
    d = death_state(m, _Params(m, CFG), A, 200, row)
    assert d.thrown_grenades == ["flashbang", "flashbang", "smokegrenade"]


def test_digest_shows_fight_blinds_and_thrown_grenades():
    from cs2coach.tools.digest import moment_line

    m = scenario(blinds=[{"tick": 80, "user_steamid": sid(C), "attacker_steamid": sid(B), "blind_duration": 1.5}],
                 fires=[fire(50, A, "weapon_flashbang"), fire(85, C), fire(90, A), fire(95, A),
                        fire(100, A), fire(150, D), fire(200, D)])
    mm = find_moments(m, A, CFG)
    assert "对手被闪（事件时剩 1.2s，最长 1.5s，目标玩家的队友闪的）" in moment_line(moment(mm, "R1-kill1"))
    assert "本回合已投道具：闪光" in moment_line(moment(mm, "R1-death"))


@pytest.mark.parametrize("subject", ["自己", "对手"])
@pytest.mark.parametrize("role,source", [("self", "目标玩家本人"), ("teammate", "目标玩家的队友"),
                                         ("opponent", "该对手本人"), ("enemy", "对手的队友"),
                                         (None, None)])
def test_digest_blind_roles_are_explicit_for_both_subjects(subject, role, source):
    from cs2coach.features.models import BlindInfo
    from cs2coach.tools.digest import _blind_line

    info = BlindInfo(blinded_at_event=True, remaining_at_event_s=0.8, max_duration_s=2.0,
                     thrower=None, thrower_ambiguous=role is None, thrower_role=role)
    expected = f"{source}闪的" if source is not None else "投掷者无法确定"
    assert _blind_line(subject, info) == f"{subject}被闪（事件时剩 0.8s，最长 2.0s，{expected}）"
