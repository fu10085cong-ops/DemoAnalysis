"""类别面板的可相加计数与多场指标。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel

from cs2coach.parsing.weapons import is_gun

if TYPE_CHECKING:
    from cs2coach.features.models import MatchMoments
    from cs2coach.parsing.match import MatchData


class PanelCounts(BaseModel):
    """单人单场原始计数；多场及分段先合并再计算。"""

    rounds: int = 0
    rounds_won: int = 0
    rounds_lost: int = 0
    kills: int = 0
    kills_won: int = 0
    deaths: int = 0
    assists: int = 0
    damage: int = 0
    damage_won: int = 0
    kill_rounds: int = 0
    multikill_rounds: int = 0
    headshot_kills: int = 0
    gun_kills: int = 0
    gun_hits: int = 0
    gun_shots: int = 0
    trade_kills: int = 0
    trade_chances: int = 0
    missed_trades: int = 0
    traded_deaths: int = 0
    opening_attempts: int = 0
    opening_kills: int = 0
    first_damage_rounds: int = 0
    opening_round_wins: int = 0
    utility_damage: int = 0
    blind_seconds: float = 0.0
    flashes: int = 0
    effective_flashes: int = 0
    flash_assists: int = 0
    grenades_held: int = 0
    grenades_used: int = 0
    clutches: int = 0
    clutch_wins: int = 0
    clutch_1v1: int = 0
    clutch_1v1_wins: int = 0
    last_alive_rounds: int = 0
    saves: int = 0
    alive_seconds_total: float = 0.0
    sniper_kills: int = 0
    sniper_rounds: int = 0
    sniper_multikill_rounds: int = 0
    sniper_opening_rounds: int = 0
    counter_strafing_stopped: int = 0
    counter_strafing_shots: int = 0
    spray_hits: int = 0
    spray_shots: int = 0
    time_to_damage_ms: list[float] = []
    crosshair_offset_m: list[float] = []
    reaction_ms: list[float] = []


@dataclass(frozen=True)
class PanelMetric:
    id: str
    category: str
    name: str
    unit: str
    scale: int
    direction: str
    kind: str
    numerator: str
    denominator: str | None
    min_samples: str


def _metric(id, category, name, unit, scale, direction, kind, numerator, denominator, minimum):
    return PanelMetric(id, category, name, unit, scale, direction, kind, numerator, denominator, minimum)


PANEL_METRICS = [
    _metric("kpr", "firepower", "每回合击杀", "次", 1, "high", "表现", "kills", "rounds", "rounds"),
    _metric("kpr_won", "firepower", "胜利回合每回合击杀", "次", 1, "high", "表现", "kills_won", "rounds_won", "rounds"),
    _metric("adr", "firepower", "回均伤害", "点", 1, "high", "表现", "damage", "rounds", "rounds"),
    _metric("adr_won", "firepower", "胜利回均伤害", "点", 1, "high", "表现", "damage_won", "rounds_won", "rounds"),
    _metric("kill_round_pct", "firepower", "击杀回合占比", "%", 100, "high", "表现", "kill_rounds", "rounds", "rounds"),
    _metric("multikill_round_pct", "firepower", "多杀回合占比", "%", 100, "high", "表现", "multikill_rounds", "rounds", "rounds"),
    _metric("damage_per_kill", "firepower", "击杀均伤害", "点", 1, "none", "风格", "damage", "kills", "kills"),
    _metric("time_to_damage_ms", "aim", "命中速度", "毫秒", 1, "low", "水平", "time_to_damage_ms", None, "aim"),
    _metric("crosshair_offset_m", "aim", "预瞄", "米", 1, "low", "水平", "crosshair_offset_m", None, "aim"),
    _metric("counter_strafing_pct", "aim", "急停", "%", 100, "high", "水平", "counter_strafing_stopped", "counter_strafing_shots", "aim"),
    _metric("spray_accuracy_pct", "aim", "压枪", "%", 100, "high", "水平", "spray_hits", "spray_shots", "aim"),
    _metric("headshot_kill_pct", "aim", "爆头击杀率", "%", 100, "high", "水平", "headshot_kills", "gun_kills", "kills"),
    _metric("accuracy_pct", "aim", "精准度", "%", 100, "high", "水平", "gun_hits", "gun_shots", "shots"),
    _metric("reaction_ms", "aim", "看见→开枪", "毫秒", 1, "low", "水平", "reaction_ms", None, "aim"),
    _metric("trade_kills_per_round", "trade", "回均补枪击杀", "次", 1, "high", "水平", "trade_kills", "rounds", "rounds"),
    _metric("trade_kill_pct", "trade", "补枪击杀占比", "%", 100, "none", "风格", "trade_kills", "kills", "kills"),
    _metric("missed_trade_pct", "trade", "漏补率", "%", 100, "low", "水平", "missed_trades", "trade_chances", "trade_chances"),
    _metric("traded_death_pct", "trade", "被补回率", "%", 100, "none", "风格", "traded_deaths", "deaths", "deaths"),
    _metric("assist_pct", "trade", "助攻占比", "%", 100, "none", "风格", "assists", "kills_plus_assists", "kills"),
    _metric("opening_attempt_pct", "opening", "首杀对枪参与率", "%", 100, "none", "风格", "opening_attempts", "rounds", "rounds"),
    _metric("opening_win_pct", "opening", "首杀对枪胜率", "%", 100, "high", "水平", "opening_kills", "opening_attempts", "opening"),
    _metric("opening_kills_per10", "opening", "每 10 回合首杀", "次", 10, "high", "水平", "opening_kills", "rounds", "rounds"),
    _metric("first_damage_per10", "opening", "每 10 回合首伤", "次", 10, "none", "风格", "first_damage_rounds", "rounds", "rounds"),
    _metric("opening_round_win_pct", "opening", "首杀后回合胜率", "%", 100, "high", "表现", "opening_round_wins", "opening_kills", "opening"),
    _metric("utility_damage_per_round", "utility", "回均道具伤害", "点", 1, "high", "水平", "utility_damage", "rounds", "rounds"),
    _metric("blind_time_per_round", "utility", "回均闪白时长", "秒", 1, "high", "水平", "blind_seconds", "rounds", "rounds"),
    _metric("effective_flash_pct", "utility", "有效闪光率", "%", 100, "high", "水平", "effective_flashes", "flashes", "flashes"),
    _metric("flash_assists_per100", "utility", "每 100 回合闪光助攻", "次", 100, "high", "水平", "flash_assists", "rounds", "rounds"),
    _metric("utility_use_pct", "utility", "道具使用率", "%", 100, "high", "水平", "grenades_used", "grenades_held", "grenades"),
    _metric("clutch_1v1_win_pct", "clutch", "1v1 胜率", "%", 100, "high", "表现", "clutch_1v1_wins", "clutch_1v1", "clutch_1v1"),
    _metric("clutch_win_pct", "clutch", "残局胜率", "%", 100, "high", "表现", "clutch_wins", "clutches", "clutches"),
    _metric("last_alive_pct", "clutch", "最后存活率", "%", 100, "none", "风格", "last_alive_rounds", "rounds", "rounds"),
    _metric("save_pct", "clutch", "失败回合存活率", "%", 100, "none", "风格", "saves", "rounds_lost", "rounds"),
    _metric("alive_seconds", "clutch", "回均存活时间", "秒", 1, "none", "风格", "alive_seconds_total", "rounds", "rounds"),
    _metric("sniper_kills_per_round", "sniper", "回均狙杀", "次", 1, "high", "水平", "sniper_kills", "rounds", "rounds"),
    _metric("sniper_kill_pct", "sniper", "狙杀占比", "%", 100, "none", "风格", "sniper_kills", "kills", "kills"),
    _metric("sniper_round_pct", "sniper", "狙杀回合占比", "%", 100, "none", "风格", "sniper_rounds", "rounds", "rounds"),
    _metric("sniper_multikill_pct", "sniper", "狙杀多杀占比", "%", 100, "high", "水平", "sniper_multikill_rounds", "sniper_rounds", "sniper_rounds"),
    _metric("sniper_opening_per10", "sniper", "每 10 回合狙击首杀", "次", 10, "high", "水平", "sniper_opening_rounds", "rounds", "rounds"),
]


def merge_panel_counts(counts: list[PanelCounts]) -> PanelCounts:
    """逐字段相加；列表保留全部交火样本。"""
    result = PanelCounts()
    for item in counts:
        for field in type(result).model_fields:
            value = getattr(item, field)
            if isinstance(value, list):
                getattr(result, field).extend(value)
            else:
                setattr(result, field, getattr(result, field) + value)
    return result


def panel_sample(counts: PanelCounts, metric: PanelMetric) -> int:
    if metric.denominator is None:
        return len(getattr(counts, metric.numerator))
    if metric.denominator == "kills_plus_assists":
        return counts.kills + counts.assists
    return int(getattr(counts, metric.denominator))


def panel_values(counts: list[PanelCounts], cfg: dict) -> dict[str, float | None]:
    merged = merge_panel_counts(counts)
    values = {}
    for metric in PANEL_METRICS:
        sample = panel_sample(merged, metric)
        minimum = cfg["panel"]["min_samples"][metric.min_samples]
        if sample < minimum or not sample:
            values[metric.id] = None
        elif metric.denominator is None:
            digits = 0 if metric.id == "time_to_damage_ms" else 2
            values[metric.id] = round(median(getattr(merged, metric.numerator)), digits)
        else:
            digits = 1 if metric.id in ("counter_strafing_pct", "spray_accuracy_pct") else 2
            values[metric.id] = round(metric.scale * getattr(merged, metric.numerator) / sample, digits)
    return values


def _enemy_damage(match: MatchData, rounds: set[int]) -> pd.DataFrame:
    d = match.damages
    return d[d["round"].isin(rounds) & d["attacker"].notna() & d["victim"].notna()
             & (d["attacker_team_num"] != d["victim_team_num"])]


def _with_round(match: MatchData, events: pd.DataFrame) -> pd.DataFrame:
    """兼容旧合成比赛未附回合列的事件；正式解析表已带回合列。"""
    if events.empty or "round" in events:
        return events
    result = events.copy()
    result["round"] = result["tick"].map(
        lambda tick: rnd.number if (rnd := match.round_of(int(tick))) is not None else None)
    return result


def _clutch(match: MatchData, sid: int, rnd, own_team: int) -> int | None:
    """由冻结期的存活名单依次扣除死亡者，首次成为最后一人时固定 N。"""
    floor = rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick
    rosters = match.__dict__.setdefault("_panel_rosters", {})
    if rnd.number not in rosters:
        roster = match.ticks[(match.ticks["tick"] == floor) & match.ticks["team_num"].isin((2, 3))
                             & match.ticks["is_alive"]]
        rosters[rnd.number] = {int(row.steamid): int(row.team_num) for row in roster.itertuples()}
    alive = rosters[rnd.number].copy()

    def size():
        own = sum(team == own_team for team in alive.values())
        enemy = sum(team != own_team for team in alive.values())
        return enemy if sid in alive and own == 1 and enemy else None

    initial = size()
    if initial is not None:
        return initial
    deaths = match.kills[(match.kills["round"] == rnd.number) & ~match.kills["in_freeze_time"]
                         & ~match.kills["after_round_end"]].sort_values("tick")
    for row in deaths.itertuples():
        if pd.notna(row.victim):
            alive.pop(int(row.victim), None)
        found = size()
        if found is not None:
            return found
    return None


def panel_counts(match: MatchData, sid: int, mm: MatchMoments, cfg: dict) -> PanelCounts:
    """按冻结期阵营及在场回合计算单人计数。"""
    from cs2coach.features.trades import trade_chance_skip
    from cs2coach.baseline import engagement_weapon

    result = PanelCounts()
    absent = set(mm.absent_rounds)
    rounds = {r.number: r for r in match.rounds if r.number not in absent}
    sides = {}
    for number, rnd in rounds.items():
        floor = rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick
        state = match.state_at(sid, floor)
        if state is not None and pd.notna(state["team_num"]) and int(state["team_num"]) in (2, 3):
            sides[number] = int(state["team_num"])
    valid = set(sides)
    won = {number for number in valid if int(rounds[number].winner) == sides[number]}
    result.rounds, result.rounds_won = len(valid), len(won)
    result.rounds_lost = result.rounds - result.rounds_won

    moments = [m for m in mm.moments if m.round in valid]
    kills = [m for m in moments if m.kind == "kill"]
    deaths = [m for m in moments if m.kind == "death"]
    result.kills, result.deaths = len(kills), len(deaths)
    result.assists = sum(m.kind == "assist" for m in moments)
    result.kills_won = sum(m.round in won for m in kills)
    kill_rounds = Counter(m.round for m in kills)
    result.kill_rounds = len(kill_rounds)
    result.multikill_rounds = sum(value >= 2 for value in kill_rounds.values())
    result.gun_kills = sum(is_gun(m.weapon) for m in kills)
    result.headshot_kills = sum(is_gun(m.weapon) and m.headshot for m in kills)
    result.trade_kills = sum("trade_kill" in m.tags for m in kills)
    chances = [c for c in mm.trade_chances if c.round in valid and trade_chance_skip(c, cfg) is None]
    result.trade_chances = len(chances)
    result.missed_trades = sum(not c.traded_by_me for c in chances)
    result.traded_deaths = sum("traded" in m.tags for m in deaths)
    result.opening_kills = sum("opening_kill" in m.tags for m in kills)
    result.opening_attempts = result.opening_kills + sum("opening_death" in m.tags for m in deaths)
    result.opening_round_wins = sum("opening_kill" in m.tags and m.round in won for m in kills)
    result.flash_assists = sum("flash_assist" in m.tags for m in moments)

    d = _enemy_damage(match, valid)
    mine = d[d["attacker"] == sid]
    result.damage = int(mine["dmg_effective"].sum())
    result.damage_won = int(mine[mine["round"].isin(won)]["dmg_effective"].sum())
    result.utility_damage = int(mine[mine["weapon"].isin(("hegrenade", "inferno", "molotov", "incgrenade"))]["dmg_effective"].sum())
    result.first_damage_rounds = sum(int(group.sort_values("tick").iloc[0]["attacker"]) == sid
                                     for _, group in d.groupby("round"))
    result.gun_hits = sum(bool(is_gun(weapon)) for weapon in mine["weapon"])
    shots = match.shots
    result.gun_shots = int((shots["round"].isin(valid) & (shots["steamid"] == sid)
                            & shots["is_gun"] & ~shots["controlling_bot"]).sum())

    grenades = match.grenades
    if grenades.empty:
        grenades = pd.DataFrame(columns=["round", "tick", "thrower", "type"])
    else:
        grenades = _with_round(match, grenades)
    thrown = grenades[(grenades["round"].isin(valid)) & (grenades["thrower"] == sid)]
    flashes = thrown[thrown["type"] == "flashbang"] if not thrown.empty else thrown
    result.flashes = len(flashes)
    blinds = _with_round(match, match.blinds)
    mine_blinds = blinds[(blinds["round"].isin(valid)) & (blinds["thrower"] == sid)
                          & ~blinds["thrower_ambiguous"] & blinds["victim"].notna()
                          & (blinds["victim_team_num"] != blinds["thrower_team_num"])
                          & blinds["victim_team_num"].isin((2, 3))
                          & blinds["thrower_team_num"].isin((2, 3))] if not blinds.empty else blinds
    result.blind_seconds = float(mine_blinds["duration"].sum()) if not mine_blinds.empty else 0.0
    # 同一投掷者同一回合内选最近且不晚于致盲的爆开 tick；一颗闪可命中多人。
    tolerance = cfg["panel"]["flash_match_ticks"]
    effective = set()
    for row in mine_blinds.itertuples():
        if row.duration < cfg["moments"]["min_blind_seconds"]:
            continue
        candidates = flashes[(flashes["round"] == row.round) & (flashes["tick"] <= row.tick)
                             & (flashes["tick"] >= row.tick - tolerance)]
        if not candidates.empty:
            effective.add(int(candidates["tick"].max()))
    result.effective_flashes = len(effective)
    economy = match.economy
    purchases = economy[(economy["round"].isin(valid)) & (economy["steamid"] == sid)] if not economy.empty else economy
    by_round = {int(row["round"]): len(row["grenades"]) for _, row in purchases.iterrows()
                if isinstance(row["grenades"], list)}
    result.grenades_held = sum(by_round.values())
    thrown_counts = Counter(int(row.round) for row in thrown.itertuples()) if not thrown.empty else Counter()
    result.grenades_used = sum(min(held, thrown_counts[number]) for number, held in by_round.items())

    sniper = set(cfg["panel"]["sniper_weapons"])
    sniper_kills = [m for m in kills if m.weapon in sniper]
    sniper_rounds = Counter(m.round for m in sniper_kills)
    result.sniper_kills = len(sniper_kills)
    result.sniper_rounds = len(sniper_rounds)
    result.sniper_multikill_rounds = sum(value >= 2 for value in sniper_rounds.values())
    result.sniper_opening_rounds = sum("opening_kill" in m.tags for m in sniper_kills)

    for number in valid:
        rnd = rounds[number]
        floor = rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick
        clue = _clutch(match, sid, rnd, sides[number])
        if clue is not None:
            result.clutches += 1
            result.last_alive_rounds += 1
            result.clutch_wins += number in won
            if clue == 1:
                result.clutch_1v1 += 1
                result.clutch_1v1_wins += number in won
        round_deaths = [m.tick for m in deaths if m.round == number and m.tick <= rnd.end_tick]
        end = min(round_deaths) if round_deaths else rnd.end_tick
        result.alive_seconds_total += max(0, end - floor) / match.meta.tick_rate
        if number not in won and not round_deaths:
            end_state = match.state_at(sid, rnd.end_tick)
            result.saves += end_state is not None and bool(end_state["is_alive"])

    aim = mm.aim_stats
    if aim is not None:
        result.time_to_damage_ms.extend(aim.time_to_damage_values_ms)
        result.crosshair_offset_m.extend(aim.crosshair_offset_values_m)
        result.counter_strafing_stopped = aim.counter_strafing_stopped
        result.counter_strafing_shots = aim.counter_strafing_shots
        result.spray_hits = aim.spray_hits
        result.spray_shots = aim.spray_shots
    # 用自己这次交火的武器筛白名单；死亡瞬间的 m.weapon 是对手的武器
    result.reaction_ms = [m.engagement.seen_to_first_shot_s * 1000 for m in moments
                          if m.engagement is not None and engagement_weapon(m) in cfg["aim_stats"]["weapons"]
                          and m.engagement.seen_to_first_shot_s is not None
                          and 0 <= m.engagement.seen_to_first_shot_s <= 1]
    return result
