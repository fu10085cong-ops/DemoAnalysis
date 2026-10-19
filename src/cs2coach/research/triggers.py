"""CT 首次获得 5v4 人数优势的触发器。"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from cs2coach.parsing.match import MatchData
from cs2coach.research.models import Situation


def round_roster(match: MatchData, round_id: int) -> dict[int, int]:
    """冻结结束时真实在场且存活的玩家，不假定每方始终五人。"""
    rnd = match.round(round_id)
    if rnd is None or rnd.freeze_end_tick is None:
        return {}
    states = match.ticks[match.ticks["tick"] == rnd.freeze_end_tick]
    return {int(row.steamid): int(row.team_num) for row in states.itertuples()
            if pd.notna(row.team_num) and int(row.team_num) in (2, 3) and bool(row.is_alive)}


def alive_at(match: MatchData, round_id: int, tick: int) -> dict[int, int]:
    """冻结期名单按死亡事件推进到指定 tick。"""
    alive = round_roster(match, round_id)
    deaths = match.kills
    if deaths.empty:
        return alive
    for row in deaths[(deaths["round"] == round_id) & (deaths["tick"] <= tick)
                      & ~deaths["in_freeze_time"] & ~deaths["after_round_end"]].itertuples():
        if pd.notna(row.victim):
            alive.pop(int(row.victim), None)
    return alive


def find_situations(match: MatchData) -> tuple[list[Situation], Counter[str]]:
    """返回触发点与每回合一个跳过原因。"""
    found: list[Situation] = []
    skipped: Counter[str] = Counter()
    for rnd in match.rounds:
        if rnd.freeze_end_tick is None:
            skipped["missing_freeze_end"] += 1
            continue
        roster = round_roster(match, rnd.number)
        deaths = match.kills
        if deaths.empty:
            skipped["no_enemy_kill"] += 1
            continue
        events = deaths[(deaths["round"] == rnd.number) & (deaths["tick"] >= rnd.freeze_end_tick)
                        & (deaths["tick"] < rnd.end_tick) & ~deaths["after_round_end"]]
        first = events[events["is_enemy_kill"]].head(1)
        if first.empty:
            skipped["no_enemy_kill"] += 1
            continue
        kill = first.iloc[0]
        alive = alive_at(match, rnd.number, int(kill["tick"]) - 1)
        # 掉线可能没有死亡事件；首杀前的在场状态必须与推演名单一致。
        complete = all((state := match.state_at(sid, int(kill["tick"]) - 1)) is not None
                       and pd.notna(state["team_num"]) and int(state["team_num"]) == team
                       and bool(state["is_alive"]) for sid, team in alive.items())
        if (sum(team == 3 for team in alive.values()) != 5 or
                sum(team == 2 for team in alive.values()) != 5 or not complete):
            skipped["not_5v5_before_first_enemy_kill"] += 1
            continue
        victim = int(kill["victim"])
        if roster.get(victim) != 2:
            skipped["first_enemy_kill_ct"] += 1
            continue
        tick = int(kill["tick"])
        bomb = match.bomb
        planted = (not bomb.empty and not bomb[(bomb["round"] == rnd.number) & (bomb["tick"] <= tick)
                                            & (bomb["event"] == "planted")].empty)
        found.append(Situation(demo_id=match.demo_path.name,
                               match_id=match.demo_path.stem.removesuffix("-p1").removesuffix("-p2"),
                               round_id=rnd.number, tick=tick, map=match.meta.map_name,
                               alive_ct=5, alive_t=4,
                               round_time_s=round((tick - rnd.freeze_end_tick) / match.meta.tick_rate, 2),
                               bomb_planted=planted,
                               first_killer_id=str(int(kill["attacker"])) if pd.notna(kill["attacker"]) else None))
    return found, skipped


def ct_first_man_advantage(match: MatchData, cfg: dict) -> list[Situation]:
    """每回合只保留首次敌对击杀直接形成的 CT 5v4。"""
    return find_situations(match)[0]
