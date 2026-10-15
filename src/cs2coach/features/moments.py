"""找出目标玩家的决定性瞬间（击杀、死亡、关键助攻），计算每个瞬间的指标。

全部是确定性计算，结果可以复核。判断轻重、写诊断由 AI 负责。
所有阈值来自配置的 [moments] 和 [counter_strafe] 段。
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from cs2coach.features.models import (
    AssistInfo,
    BlindInfo,
    Context,
    DeathState,
    Engagement,
    Excluded,
    MatchMoments,
    MissedTrade,
    Moment,
    Shot,
    Trade,
)
from cs2coach.features.aim import scoped_before_shot, shot_targets
from cs2coach.features.aim_stats import aim_stats
from cs2coach.features.visibility import Sight, angle_between, blinded_at
from cs2coach.parsing.match import MatchData
from cs2coach.parsing.weapons import is_gun
from cs2coach.parsing.models import Round, Team


def find_moments(match: MatchData, steamid: int, cfg: dict[str, Any], sight: Sight | None = None) -> MatchMoments:
    """sight：可注入（测试用）；默认按地图加载 awpy 的地图网格。"""
    p = _Params(match, cfg)
    p.shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    p.sight = sight if sight is not None else Sight(match, cfg)
    k = match.kills
    moments: list[Moment] = []
    excluded: list[Excluded] = []

    mine_kill = (k["attacker"] == steamid).fillna(False)
    mine_death = (k["victim"] == steamid).fillna(False)
    for _, row in k[mine_kill | mine_death].iterrows():
        # 摔死、炸死时 attacker 为空，击杀 bot 时 victim 为空，不能直接比较
        kind = "kill" if _int_or_none(row["attacker"]) == steamid else "death"
        reason = _exclusion(row, kind)
        if reason:
            excluded.append(Excluded(round=int(row["round"]), tick=int(row["tick"]), kind=kind, reason=reason))
            continue
        moments.append(_kill_or_death(match, p, steamid, row, kind))

    assists = k[(k["assister"] == steamid).fillna(False) & k["assist_counts"] & ~k["in_freeze_time"]]
    for _, row in assists.iterrows():
        m = _assist(match, p, steamid, row)
        if m is not None:
            moments.append(m)

    moments.sort(key=lambda m: (m.tick, m.kind != "death"))
    _assign_ids(moments)
    player = match.players[int(steamid)]
    return MatchMoments(
        demo=match.demo_path.name, map_name=match.meta.map_name, tick_rate=match.meta.tick_rate,
        player=int(steamid), player_name=player.name, rounds=len(match.rounds),
        moments=moments, missed_trades=find_missed_trades(match, p, steamid), excluded=excluded,
        aim_stats=aim_stats(match, steamid, cfg, p.shots, p.sight, moments),
        visibility_data_version=p.sight.data_version, visibility_unavailable=p.sight.unavailable_reason,
        absent_rounds=list(player.absent_rounds),
    )


class _Params:
    def __init__(self, match: MatchData, cfg: dict[str, Any]):
        mc, cs = cfg["moments"], cfg["counter_strafe"]
        self.rate = match.meta.tick_rate
        self.gap = round(mc["engagement_gap_seconds"] * self.rate)
        self.max_len = round(mc["engagement_max_seconds"] * self.rate)
        self.trade = round(mc["trade_window_seconds"] * self.rate)
        self.missed_trade_distance = float(mc["missed_trade_max_distance"])
        self.key_assist_damage = int(mc["key_assist_min_damage"])
        self.min_blind = float(mc["min_blind_seconds"])
        self.blind_min_remaining = float(cfg["visibility"]["blind_min_remaining_seconds"])
        self.jump_lead = round(mc["jump_lead_seconds"] * self.rate)
        self.still_speed = float(mc["contact_still_speed"])
        self.min_shot_interval: dict[str, float] = cfg["weapons"]["min_shot_interval_seconds"]
        self.moving_speed = float(mc["contact_moving_speed"])
        self.contact_window = round(mc["contact_speed_window_seconds"] * self.rate)
        self.stop_ratio = float(cs["accurate_speed_ratio"])
        self.crouch = float(cs["crouch_duck_amount"])
        self.max_speed: dict[str, float] = cs["max_speed"]
        self.max_speed_scoped: dict[str, float] = cs["max_speed_scoped"]
        self.shots = match.shots  # find_moments 中替换为带 target 列的版本
        self.sight: Sight | None = None

    def secs(self, ticks: float) -> float:
        return round(ticks / self.rate, 3)


def _exclusion(row: pd.Series, kind: str) -> str | None:
    if row["in_freeze_time"]:
        return "冻结期内的自杀，不是对局行为"
    if kind == "kill" and row["by_bot_control"]:
        return "接管 bot 时的击杀：这名玩家自己的坐标停在死亡位置，无法分析"
    if kind == "death" and row["victim_bot_control"]:
        return "接管 bot 时阵亡：死的是 bot，无法分析这名玩家"
    if kind == "kill" and row["is_suicide"]:
        return "自杀"
    return None


# ---------- 击杀 / 死亡 ----------

def _kill_or_death(match: MatchData, p: _Params, sid: int, row: pd.Series, kind: str) -> Moment:
    tick, rnd = int(row["tick"]), match.round(int(row["round"]))
    opp_col = "victim" if kind == "kill" else "attacker"
    opponent = _int_or_none(row[opp_col])
    lower = _engagement_floor(match, sid, rnd, tick)
    eng = engagement(match, p, sid, opponent, tick, lower) if opponent is not None else None
    if eng is not None:
        eng = add_visibility(match, p, sid, opponent, tick, lower, kind, eng)
    ctx = context(match, sid, opponent, tick, rnd, row, kind, eng)

    trade = trade_info(match, p, sid, opponent, tick, kind)
    death = death_state(match, p, sid, tick, row) if kind == "death" else None
    tags = _tags(match, row, kind, ctx, eng, trade, death)
    earliest = tick
    if eng is not None:
        earliest = min(t for t in (eng.start_tick, eng.player_first_seen_tick, eng.opponent_first_seen_tick) if t is not None)
    jump = max(rnd.start_tick, earliest - p.jump_lead)
    return Moment(
        id="", kind=kind, round=rnd.number, tick=tick, player=sid, opponent=opponent,
        opponent_name=match.name_of(opponent) if opponent is not None else None,
        weapon=row["weapon"], headshot=bool(row["headshot"]), through_smoke=bool(row["thrusmoke"]),
        wallbang=bool(row["penetrated"]), noscope=bool(row["noscope"]), attacker_blind=bool(row["attackerblind"]),
        tags=tags, context=ctx, engagement=eng, trade=trade, death=death, assist=None,
        jump_tick=jump, jump_command=f"demo_gototick {jump}",
    )


def _engagement_floor(match: MatchData, sid: int, rnd: Round, tick: int) -> int:
    """交火最早从哪里开始：冻结期结束，且不早于目标玩家本回合上一次击杀（之前的开枪属于上一次交火）。"""
    floor = rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick
    k = match.kills
    prev = k[(k["attacker"] == sid).fillna(False) & (k["round"] == rnd.number) & (k["tick"] < tick)]
    if len(prev):
        floor = max(floor, int(prev["tick"].max()) + 1)
    return floor


def engagement(match: MatchData, p: _Params, sid: int, opponent: int, tick: int, floor: int) -> Engagement:
    """只算双方互相瞄准的开枪（按子弹方向判断目标，见 features/aim.py），以及命中对方的开枪。"""
    lo = max(floor, tick - p.max_len)
    s, d = p.shots, match.damages
    pair = d["tick"].between(lo, tick)
    dealt = d[pair & (d["attacker"] == sid).fillna(False) & (d["victim"] == opponent).fillna(False)]
    taken = d[pair & (d["attacker"] == opponent).fillna(False) & (d["victim"] == sid).fillna(False)]
    in_win = s["tick"].between(lo, tick) & s["is_gun"] & ~s["controlling_bot"]
    my_shots = s[in_win & (s["steamid"] == sid).fillna(False)
                 & ((s["target"] == opponent).fillna(False) | s["tick"].isin(dealt["tick"]))]
    opp_shots = s[in_win & (s["steamid"] == opponent).fillna(False)
                  & ((s["target"] == sid).fillna(False) | s["tick"].isin(taken["tick"]))]

    # 从瞬间往前，把间隔不超过 gap 的活动串起来
    activity = sorted({tick, *my_shots["tick"], *opp_shots["tick"], *dealt["tick"], *taken["tick"]}, reverse=True)
    start = tick
    for t in activity:
        if start - t <= p.gap:
            start = int(t)
        else:
            break
    my_shots = my_shots[my_shots["tick"] >= start]
    opp_shots = opp_shots[opp_shots["tick"] >= start]
    dealt = dealt[dealt["tick"] >= start]
    taken = taken[taken["tick"] >= start]

    states = match.player_ticks(sid, start - 1, tick).set_index("tick")
    hit_ticks = set(dealt["tick"].astype(int))
    shots = [_shot(p, r, states, hit_ticks) for _, r in my_shots.iterrows()]
    cs = [x for x in shots if not x.crouched and not x.airborne and x.stopped is not None]
    first = shots[0] if shots else None
    my_first = int(my_shots["tick"].min()) if len(my_shots) else None
    opp_first = int(opp_shots["tick"].min()) if len(opp_shots) else None
    return Engagement(
        start_tick=start, end_tick=tick, duration_s=p.secs(tick - start),
        shots=shots, shots_fired=len(shots), shots_hit=sum(x.hit for x in shots),
        first_shot_hit=first.hit if first else None,
        damage_dealt=int(dealt["dmg_effective"].sum()), damage_taken=int(taken["dmg_effective"].sum()),
        player_first_shot_tick=my_first, opponent_first_shot_tick=opp_first,
        first_shot_lead_s=p.secs(opp_first - my_first) if my_first is not None and opp_first is not None else None,
        counter_strafe_shots=len(cs), counter_strafe_stopped=sum(bool(x.stopped) for x in cs),
        first_shot_stopped=(first.stopped if first and first in cs else None),
        player_blind=blind_info(match, p, sid, sid, opponent, start, tick),
        opponent_blind=blind_info(match, p, opponent, sid, opponent, start, tick),
    )


def blind_info(match: MatchData, p: _Params, victim: int, sid: int, opponent: int,
               start_tick: int, tick: int) -> BlindInfo | None:
    """取与交火窗口重叠的致盲；事件时仍被闪取剩余最长的一次，否则取时长最长的一次。"""
    b = match.blinds
    if b.empty:
        return None
    b = b[(b["victim"] == victim).fillna(False) & (b["duration"] >= p.min_blind)
          & (b["tick"] <= tick) & (b["tick"] + b["duration"] * p.rate > start_tick)]
    if b.empty:
        return None
    remaining = (b["tick"] + b["duration"] * p.rate - tick) / p.rate
    best = b.loc[remaining.idxmax()] if remaining.max() > 0 else b.loc[b["duration"].idxmax()]
    left = (int(best["tick"]) + float(best["duration"]) * p.rate - tick) / p.rate
    ambiguous = bool(best["thrower_ambiguous"])
    thrower = None if ambiguous else _int_or_none(best["thrower"])
    role = None
    if thrower == sid:
        role = "self"
    elif thrower == opponent:
        role = "opponent"
    elif thrower is not None:
        st = match.state_at(sid, int(best["tick"]))
        team = _int_or_none(st["team_num"]) if st is not None else None
        thrower_team = _int_or_none(best["thrower_team_num"])
        if team in (Team.T, Team.CT) and thrower_team in (Team.T, Team.CT):
            role = "teammate" if team == thrower_team else "enemy"
    return BlindInfo(
        blinded_at_event=left > 0, remaining_at_event_s=round(left, 2) if left > 0 else None,
        max_duration_s=float(b["duration"].max()), thrower=thrower,
        thrower_ambiguous=ambiguous, thrower_role=role,
    )


def add_visibility(match: MatchData, p: _Params, sid: int, opponent: int, tick: int, floor: int,
                   kind: str, eng: Engagement) -> Engagement:
    """补上视野指标：谁先看见谁、Time to Damage（及拆分）、Crosshair Placement。"""
    sight = p.sight
    if sight is None or not sight.available:
        return eng.model_copy(update={"visibility_note": "no_map_data"})
    d = match.damages
    # Time to Damage、Crosshair Placement 只看枪械伤害（手雷、燃烧弹的伤害与枪法无关）
    win = d["tick"].between(eng.start_tick, tick) & d["weapon"].map(is_gun)
    dealt = d[win & (d["attacker"] == sid).fillna(False) & (d["victim"] == opponent).fillna(False)]
    taken = d[win & (d["attacker"] == opponent).fillna(False) & (d["victim"] == sid).fillna(False)]
    my_dmg = int(dealt["tick"].min()) if len(dealt) else None
    opp_dmg = int(taken["tick"].min()) if len(taken) else None
    # 没造成伤害时，锚定在瞬间前一 tick（死亡那一 tick 已经是死亡状态）
    me = sight.first_seen(sid, opponent, my_dmg if my_dmg is not None else tick - 1, floor)
    opp = sight.first_seen(opponent, sid, opp_dmg if opp_dmg is not None else tick - 1, floor)
    me_blind = blinded_at(match, p, sid, me.tick) if me.tick is not None else None
    opp_blind = blinded_at(match, p, opponent, opp.tick) if opp.tick is not None else None

    def valid(fs):
        return fs.tick is not None and not fs.smoke

    note = None
    if not valid(me):
        if me.smoke:
            note = "smoke"
        elif me.seen_at_limit:
            # 回溯停在下限：下限是本回合上一次击杀（之后的才属于这次交火）或冻结期结束，不是"超过 3 秒"
            rnd = match.round_of(tick)
            round_floor = (rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick) if rnd else None
            if me.limit == "floor":
                note = "seen_since_round_start" if floor == round_floor else "seen_since_previous_kill"
            else:
                note = "seen_before_window"
        else:
            note = "not_seen"
    if me_blind and note != "smoke":
        note = "blind"
    upd: dict[str, Any] = {
        "player_first_seen_tick": me.tick,
        "opponent_first_seen_tick": opp.tick,
        "player_seen_while_blind": me_blind,
        "opponent_seen_while_blind": opp_blind,
        "visibility_note": note,
    }
    if valid(me) and valid(opp) and not me_blind and not opp_blind:
        upd["seen_first_lead_s"] = p.secs(opp.tick - me.tick)
    seen_ticks = [fs.tick for fs in (me, opp) if valid(fs)]
    if seen_ticks:
        contact = min(seen_ticks)
        mine, theirs = _avg_speed(match, sid, contact, p.contact_window), _avg_speed(match, opponent, contact, p.contact_window)
        upd["contact_tick"] = contact
        upd["player_speed_at_contact"] = mine
        upd["opponent_speed_at_contact"] = theirs
        upd["contact_type"] = contact_type(mine, theirs, p.still_speed, p.moving_speed)
    if valid(me) and not me_blind and my_dmg is not None:
        upd["time_to_damage_s"] = p.secs(my_dmg - me.tick)
        upd["crosshair_placement_deg"] = angle_between(sight.view_vector(sid, me.tick), sight.view_vector(sid, my_dmg))
    if valid(opp) and not opp_blind and opp_dmg is not None:
        upd["opponent_time_to_damage_s"] = p.secs(opp_dmg - opp.tick)
    if valid(me):
        shot_ticks = [s.tick for s in eng.shots]
        after = [t for t in shot_ticks if t >= me.tick]
        upd["shots_before_seen"] = len(shot_ticks) - len(after)
        if after and not me_blind:
            upd["seen_to_first_shot_s"] = p.secs(after[0] - me.tick)
            if my_dmg is not None and my_dmg >= after[0]:
                upd["first_shot_to_damage_s"] = p.secs(my_dmg - after[0])
    if kind == "kill" and my_dmg is not None:
        upd["damage_to_kill_s"] = p.secs(tick - my_dmg)
    return eng.model_copy(update=upd)


def _avg_speed(match: MatchData, sid: int, tick: int, window: int) -> float | None:
    """[tick - window, tick] 内的平均水平速度。"""
    v = match.player_ticks(sid, tick - window, tick)["speed_xy"].dropna()
    return round(float(v.mean()), 1) if len(v) else None


def contact_type(mine: float | None, theirs: float | None, still: float, moving: float) -> str | None:
    """接触时谁在移动：只在双方状态都明确（静止或移动）时下结论，处在减速、急停等过渡状态时为 unclear。"""
    if mine is None or theirs is None:
        return None

    def state(v: float) -> str | None:
        return "still" if v < still else "moving" if v >= moving else None

    a, b = state(mine), state(theirs)
    if a is None or b is None:
        return "unclear"
    return {("moving", "still"): "peek", ("still", "moving"): "held",
            ("moving", "moving"): "both_moving", ("still", "still"): "both_still"}[(a, b)]


def _shot(p: _Params, r: pd.Series, states: pd.DataFrame, hit_ticks: set[int]) -> Shot:
    t = int(r["tick"])
    st = states.loc[t] if t in states.index else None
    speed = _float(st["speed_xy"]) if st is not None else None
    scoped = scoped_before_shot(states, t)
    weapon = r["weapon"]
    max_speed = (p.max_speed_scoped.get(weapon) if scoped else None) or p.max_speed.get(weapon)
    ratio = round(speed / max_speed, 3) if speed is not None and max_speed else None
    return Shot(
        tick=t, weapon=weapon, speed=round(speed, 1) if speed is not None else None, max_speed=max_speed,
        speed_ratio=ratio, stopped=(ratio < p.stop_ratio) if ratio is not None else None,
        crouched=bool(st is not None and _float(st["duck_amount"]) is not None and st["duck_amount"] >= p.crouch),
        airborne=bool(st is not None and st["is_airborne"]), scoped=scoped,
        recoil_index=_float(r["recoil_index"]), hit=t in hit_ticks,
        aim_error_deg=_float(r.get("target_angle")),
    )


def context(match: MatchData, sid: int, opponent: int | None, tick: int, rnd: Round, row: pd.Series,
            kind: str, eng: Engagement | None) -> Context:
    me = match.state_at(sid, tick - 1)
    my_team = _int_or_none(me["team_num"]) if me is not None else None
    own = enemy = 0
    for other in match.players:
        st = match.state_at(other, tick - 1)
        if st is None or not st["is_alive"]:
            continue
        if _int_or_none(st["team_num"]) == my_team:
            own += 1
        else:
            enemy += 1
    opp = match.state_at(opponent, tick - 1) if opponent is not None else None
    before = (eng.start_tick if eng else tick) - 1
    me_before = match.state_at(sid, before)
    opp_before = match.state_at(opponent, before) if opponent is not None else None
    me_role, opp_role = ("attacker", "victim") if kind == "kill" else ("victim", "attacker")
    start = rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick
    return Context(
        side="T" if my_team == Team.T else "CT",
        alive_own=own, alive_enemy=enemy,
        round_time_s=round((tick - start) / match.meta.tick_rate, 2),
        bomb_planted=bomb_planted(match, rnd.number, tick),
        own_equip_value=_equip(match, rnd.number, sid), opponent_equip_value=_equip(match, rnd.number, opponent),
        own_weapon=_str(me["active_weapon_name"]) if me is not None else None,
        opponent_weapon=_str(opp["active_weapon_name"]) if opp is not None else None,
        own_health_before=_int_or_none(me_before["health"]) if me_before is not None else None,
        opponent_health_before=_int_or_none(opp_before["health"]) if opp_before is not None else None,
        distance=_float(row["distance"]),
        place=_str(row.get(f"{me_role}_last_place_name")),
        opponent_place=_str(row.get(f"{opp_role}_last_place_name")),
    )


def bomb_planted(match: MatchData, round_number: int, tick: int) -> bool:
    b = match.bomb
    if b.empty:
        return False
    b = b[(b["round"] == round_number) & (b["tick"] < tick)]
    if b.empty:
        return False
    return b.sort_values("tick")["event"].iloc[-1] == "planted"


def trade_info(match: MatchData, p: _Params, sid: int, opponent: int | None, tick: int, kind: str) -> Trade | None:
    if opponent is None:
        return None
    k = match.kills[match.kills["is_enemy_kill"] & ~match.kills["in_freeze_time"]]
    mates = _teammates(match, sid, tick - 1)
    if kind == "death":
        after = k[(k["victim"] == opponent).fillna(False) & k["tick"].between(tick + 1, tick + p.trade)
                  & k["attacker"].isin(mates)]
        first = after.sort_values("tick").iloc[0] if len(after) else None
        return Trade(
            traded=first is not None,
            traded_by=int(first["attacker"]) if first is not None else None,
            trade_delay_s=p.secs(int(first["tick"]) - tick) if first is not None else None,
            nearest_teammate_distance=_nearest_teammate(match, sid, mates, tick - 1),
        )
    before = k[(k["attacker"] == opponent).fillna(False) & k["tick"].between(tick - p.trade, tick - 1)
               & k["victim"].isin(mates)]
    last = before.sort_values("tick").iloc[-1] if len(before) else None
    return Trade(
        is_trade_kill=last is not None,
        avenged_teammate=int(last["victim"]) if last is not None else None,
        avenge_delay_s=p.secs(tick - int(last["tick"])) if last is not None else None,
    )


def death_state(match: MatchData, p: _Params, sid: int, tick: int, row: pd.Series) -> DeathState:
    b = match.blinds
    b = b[(b["victim"] == sid).fillna(False) & (b["tick"] <= tick) & (b["duration"] >= p.min_blind)]
    best = None
    for _, r in b.iterrows():
        remaining = (int(r["tick"]) + r["duration"] * p.rate - tick) / p.rate
        if remaining > 0 and (best is None or remaining > best[0]):
            best = (remaining, r)
    blinded_by = _int_or_none(best[1]["thrower"]) if best else None
    by_mate = None
    if best is not None:
        by_mate = bool(best[1]["thrower_team_num"] == best[1]["victim_team_num"]) if blinded_by is not None else None
    grenades = row.get("victim_prev_grenades")
    ready = weapon_readiness(p, sid, tick)
    s = p.shots
    thrown = s[(s["steamid"] == sid).fillna(False) & (s["round"] == int(row["round"]))
               & (s["tick"] < tick) & s["is_grenade"] & ~s["controlling_bot"]]
    return DeathState(
        **ready,
        unused_grenades=list(grenades) if isinstance(grenades, list) else [],
        thrown_grenades=thrown.sort_values("tick", kind="stable")["weapon"].tolist(),
        blinded=best is not None, blind_remaining_s=round(best[0], 2) if best else None,
        blinded_by=blinded_by, blinded_by_teammate=by_mate,
    )


def weapon_readiness(p: _Params, sid: int, tick: int) -> dict[str, Any]:
    """阵亡时手里的枪是否来得及再开一枪：看本人最后一枪（任何目标）到阵亡的间隔。"""
    s = p.shots
    mine = s[(s["steamid"] == sid).fillna(False) & s["is_gun"] & (s["tick"] < tick)]
    if mine.empty:
        return {}
    last = mine.sort_values("tick").iloc[-1]
    interval = p.min_shot_interval.get(last["weapon"])
    gap = (tick - int(last["tick"])) / p.rate
    if interval is None or gap > 2 * interval:
        return {}
    return {"weapon_ready": gap >= interval, "last_shot_weapon": last["weapon"],
            "last_shot_gap_s": round(gap, 3), "min_shot_interval_s": interval}


def _tags(match: MatchData, row: pd.Series, kind: str, ctx: Context, eng: Engagement | None,
          trade: Trade | None, death: DeathState | None) -> list[str]:
    tags = []
    k = match.kills
    rk = k[(k["round"] == row["round"]) & k["is_enemy_kill"] & ~k["in_freeze_time"]]
    if len(rk) and int(rk["tick"].min()) == int(row["tick"]) and row["is_enemy_kill"]:
        tags.append("opening_kill" if kind == "kill" else "opening_death")
    if ctx.alive_own == 1 and ctx.alive_enemy >= 1:
        tags.append(f"clutch_1v{ctx.alive_enemy}")
    if ctx.alive_own > ctx.alive_enemy:
        tags.append("man_advantage")
    elif ctx.alive_own < ctx.alive_enemy:
        tags.append("man_disadvantage")
    if ctx.bomb_planted:
        tags.append("post_plant")
    if row["after_round_end"]:
        tags.append("after_round_end")
    if row["is_teamkill"]:
        tags.append("teamkill")
    if kind == "kill" and row["victim_unknown"]:
        tags.append("victim_is_bot")
    if kind == "death" and row["is_world"]:
        tags.append("world_damage")
    if trade is not None:
        if kind == "death" and trade.traded is not None:
            tags.append("traded" if trade.traded else "not_traded")
        if kind == "kill" and trade.is_trade_kill:
            tags.append("trade_kill")
    if kind == "death" and eng is not None and eng.shots_fired == 0:
        tags.append("no_shots_fired")
    if eng is not None and eng.opponent_blind is not None:
        tags.append("opponent_blind")
    if eng is not None and eng.player_blind is not None:
        tags.append("blind_in_fight")
    if eng is not None and eng.seen_first_lead_s is not None and eng.seen_first_lead_s != 0:
        tags.append("saw_opponent_first" if eng.seen_first_lead_s > 0 else "opponent_saw_first")
    if eng is not None and eng.contact_type in ("peek", "held"):
        tags.append("peeked_held_angle" if eng.contact_type == "peek" else "held_angle")
    if kind == "death" and eng is not None and eng.visibility_note == "not_seen":
        tags.append("died_without_seeing")
    if eng is not None and eng.first_shot_lead_s is not None:
        tags.append("shot_first" if eng.first_shot_lead_s > 0 else "opponent_shot_first" if eng.first_shot_lead_s < 0 else "shot_same_tick")
    if death is not None:
        if death.weapon_ready is False:
            tags.append("weapon_not_ready")
        if death.blinded:
            tags.append("died_blind")
        if death.unused_grenades:
            tags.append("unused_utility")
    return tags


# ---------- 关键助攻 ----------

def _assist(match: MatchData, p: _Params, sid: int, row: pd.Series) -> Moment | None:
    tick, rnd = int(row["tick"]), match.round(int(row["round"]))
    victim = _int_or_none(row["victim"])
    d = match.damages
    dmg = int(d[(d["round"] == rnd.number) & (d["tick"] <= tick) & (d["attacker"] == sid).fillna(False)
                & (d["victim"] == victim).fillna(False)]["dmg_effective"].sum()) if victim is not None else 0
    flash = bool(row["assistedflash"])
    if not flash and dmg < p.key_assist_damage:
        return None
    ctx = context(match, sid, victim, tick, rnd, row, "kill", None)
    tags = ["flash_assist" if flash else "damage_assist"]
    if ctx.bomb_planted:
        tags.append("post_plant")
    jump = max(rnd.start_tick, tick - p.jump_lead)
    return Moment(
        id="", kind="assist", round=rnd.number, tick=tick, player=sid, opponent=victim,
        opponent_name=match.name_of(victim) if victim is not None else None,
        weapon=row["weapon"], headshot=bool(row["headshot"]), through_smoke=bool(row["thrusmoke"]),
        wallbang=bool(row["penetrated"]), noscope=bool(row["noscope"]), attacker_blind=bool(row["attackerblind"]),
        tags=tags, context=ctx.model_copy(update={"place": None}), engagement=None, trade=None, death=None,
        assist=AssistInfo(flash_assist=flash, damage_to_victim=dmg, killer=_int_or_none(row["attacker"])),
        jump_tick=jump, jump_command=f"demo_gototick {jump}",
    )


# ---------- 漏补 ----------

def find_missed_trades(match: MatchData, p: _Params, sid: int) -> list[MissedTrade]:
    k = match.kills
    ok = k["is_enemy_kill"] & ~k["in_freeze_time"] & ~k["after_round_end"] & k["attacker"].notna()
    out = []
    for _, r in k[ok & (k["victim"] != sid).fillna(False)].iterrows():
        tick, victim, killer = int(r["tick"]), int(r["victim"]), int(r["attacker"])
        me = match.state_at(sid, tick - 1)
        if me is None or not me["is_alive"] or me["controlling_bot"]:
            continue
        if _int_or_none(me["team_num"]) != _int_or_none(r["victim_team_num"]):
            continue
        vs = match.state_at(victim, tick - 1)
        if vs is None:
            continue
        dist = _dist(me, vs)
        if dist is None or dist > p.missed_trade_distance:
            continue
        revenge = k[ok & (k["victim"] == killer).fillna(False) & k["tick"].between(tick + 1, tick + p.trade)]
        if (revenge["attacker"] == sid).any():
            continue
        if not _spotted_by(match, killer, sid, tick, tick + p.trade):
            continue
        out.append(MissedTrade(round=int(r["round"]), tick=tick, teammate=victim, killer=killer,
                               distance=round(dist, 1), killer_traded_by_other=len(revenge) > 0))
    return out


# ---------- 工具函数 ----------

def _spotted_by(match: MatchData, target: int, viewer: int, start: int, end: int) -> bool:
    """[start, end] 内 target 是否在雷达上被 viewer 发现过。"""
    bit = match.spotted_bit(viewer)
    if bit is None:
        return False
    masks = match.player_ticks(target, start, end)["spotted_by_mask"].to_numpy()
    return bool(((masks >> bit) & 1).any())


def _teammates(match: MatchData, sid: int, tick: int) -> list[int]:
    me = match.state_at(sid, tick)
    team = _int_or_none(me["team_num"]) if me is not None else None
    out = []
    for other in match.players:
        if other == sid:
            continue
        st = match.state_at(other, tick)
        if st is not None and _int_or_none(st["team_num"]) == team:
            out.append(other)
    return out


def _nearest_teammate(match: MatchData, sid: int, mates: list[int], tick: int) -> float | None:
    me = match.state_at(sid, tick)
    if me is None:
        return None
    dists = []
    for m in mates:
        st = match.state_at(m, tick)
        if st is not None and st["is_alive"]:
            d = _dist(me, st)
            if d is not None:
                dists.append(d)
    return round(min(dists), 1) if dists else None


def _dist(a: pd.Series, b: pd.Series) -> float | None:
    vals = [_float(a[c]) for c in "XYZ"] + [_float(b[c]) for c in "XYZ"]
    if any(v is None for v in vals):
        return None
    return math.dist(vals[:3], vals[3:])


def _equip(match: MatchData, round_number: int, sid: int | None) -> int | None:
    if sid is None or match.economy.empty:
        return None
    e = match.economy
    r = e[(e["round"] == round_number) & (e["steamid"] == sid)]
    return _int_or_none(r["current_equip_value"].iloc[0]) if len(r) else None


def _assign_ids(moments: list[Moment]) -> None:
    counters: dict[tuple[int, str], int] = {}
    for m in moments:
        key = (m.round, m.kind)
        counters[key] = counters.get(key, 0) + 1
        n = counters[key]
        m.id = f"R{m.round}-{m.kind}" + (str(n) if m.kind != "death" else "")


def _int_or_none(v: object) -> int | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v: object) -> float | None:
    if v is None or v is pd.NA:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _str(v: object) -> str | None:
    if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return None
    return str(v)
