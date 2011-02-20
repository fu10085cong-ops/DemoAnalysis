"""确定性、可回溯的 CS2 demo 特征计算。"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class AnalysisConfig:
    trade_window_seconds: float = 5.0
    stop_speed_ratio: float = 0.34
    crouch_amount_threshold: float = 0.5
    min_flash_duration_seconds: float = 0.5
    pre_event_seconds: float = 3.0
    min_pattern_samples: int = 3


def steamid(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def truth(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def team_relation(team_a: Any, team_b: Any) -> str:
    a, b = number(team_a), number(team_b)
    if a is None or b is None:
        return "unknown"
    return "teammate" if int(a) == int(b) else "enemy"


def _row_at(ticks: pd.DataFrame, sid: int | None, tick: int) -> dict[str, Any]:
    if sid is None or ticks.empty or "steamid" not in ticks or "tick" not in ticks:
        return {}
    lookup = ticks.attrs.get("row_lookup")
    if lookup is not None:
        return lookup.get((sid, tick), {})
    rows = ticks[(ticks["steamid"].map(steamid) == sid) & (ticks["tick"].astype("int64") == tick)]
    return rows.iloc[0].to_dict() if not rows.empty else {}


def _round_at(rounds: list[dict[str, Any]], tick: int) -> int | None:
    for item in rounds:
        start = item.get("start_tick")
        end = item.get("end_tick")
        if start is not None and tick >= start and (end is None or tick <= end):
            return item["round"]
    return None


def build_rounds(state: pd.DataFrame, freeze_ticks: list[int]) -> list[dict[str, Any]]:
    if state.empty or "tick" not in state:
        return []
    status_col = next((c for c in state.columns if c.endswith("m_iRoundWinStatus")), None)
    if status_col is None:
        raise ValueError("缺少 CCSGameRulesProxy.CCSGameRules.m_iRoundWinStatus，无法按引擎回合状态切分")
    ordered = state.sort_values("tick").drop_duplicates("tick").reset_index(drop=True)
    status = pd.to_numeric(ordered[status_col], errors="coerce").fillna(0)
    ended = ordered[(status.ne(0)) & status.shift(fill_value=0).eq(0)]
    warm_col = "is_warmup_period"
    starts: list[int] = []
    for tick in sorted(set(freeze_ticks)):
        row = ordered[ordered["tick"].astype("int64") == tick]
        if row.empty:
            continue
        is_warmup = truth(row.iloc[0].get(warm_col, False))
        if not is_warmup:
            starts.append(tick)

    ends = []
    for _, row in ended.iterrows():
        if truth(row.get(warm_col, False)):
            continue
        value = int(number(row.get(status_col)) or 0)
        ends.append({
            "end_tick": int(row["tick"]),
            "winner_team_num": value if value in (2, 3) else None,
            "reason": number(row.get(next((c for c in row.index if c.endswith("m_eRoundWinReason")), ""))),
        })

    rounds = []
    unused_starts = list(starts)
    previous_end = None
    rounds_played_col = "total_rounds_played"
    for index, end in enumerate(ends, start=1):
        eligible = [tick for tick in unused_starts if tick <= end["end_tick"] and (previous_end is None or tick > previous_end)]
        if eligible:
            start = min(eligible)
            unused_starts.remove(start)
            estimated_start = False
        else:
            start = 0 if previous_end is None else previous_end + 1
            estimated_start = True
        end_row = ordered[ordered["tick"].astype("int64") == end["end_tick"]]
        raw_round = number(end_row.iloc[0].get(rounds_played_col)) if not end_row.empty else None
        round_number = int(raw_round) if raw_round is not None and raw_round > 0 else index
        rounds.append({"round": round_number, "start_tick": start, "start_estimated": estimated_start, **end})
        previous_end = end["end_tick"]

    for start in unused_starts:
        if previous_end is None or start > previous_end:
            rounds.append({"round": len(rounds) + 1, "start_tick": start, "start_estimated": False,
                           "end_tick": None, "winner_team_num": None, "reason": None})
    return rounds


def _event_id(row: dict[str, Any], column: str) -> int | None:
    return steamid(row.get(column))


def _make_moment(kind: str, event: dict[str, Any], target_id: int, rounds: list[dict[str, Any]], tick_rate: float,
                 pre_event_seconds: float, **details: Any) -> dict[str, Any]:
    tick = int(event["tick"])
    jump_tick = max(0, tick - round(pre_event_seconds * tick_rate))
    return {
        "kind": kind,
        "tick": tick,
        "time_seconds": round(tick / tick_rate, 3),
        "round": _round_at(rounds, tick),
        "player_steamid": target_id,
        "event": details,
        "evidence": {"event_tick": tick, "jump_tick": jump_tick,
                     "jump_command": f"demo_gototick {jump_tick}"},
    }


def _team_for(ticks: pd.DataFrame, sid: int | None, tick: int) -> int | None:
    row = _row_at(ticks, sid, tick)
    value = number(row.get("team_num"))
    return int(value) if value is not None else None


def _trades(deaths: pd.DataFrame, ticks: pd.DataFrame, target_id: int, rounds: list[dict[str, Any]], tick_rate: float,
            window: float) -> dict[str, Any]:
    events = []
    if deaths.empty:
        return {"team_kills_traded": 0, "target_deaths_traded": 0, "team_kill_trade_rate": None,
                "target_death_trade_rate": None, "window_seconds": window, "examples": []}
    records = deaths.to_dict(orient="records")
    records.sort(key=lambda row: int(row["tick"]))
    deaths_by_victim: dict[int, list[dict[str, Any]]] = {}
    for row in records:
        victim = _event_id(row, "user_steamid")
        if victim is not None:
            deaths_by_victim.setdefault(victim, []).append(row)
    victim_ticks = {sid: [int(row["tick"]) for row in rows] for sid, rows in deaths_by_victim.items()}
    for row in records:
        tick = int(row["tick"])
        victim = _event_id(row, "user_steamid")
        attacker = _event_id(row, "attacker_steamid")
        if attacker is None or victim is None or attacker == victim:
            continue
        victim_team = _team_for(ticks, victim, tick)
        attacker_team = _team_for(ticks, attacker, tick)
        target_team = _team_for(ticks, target_id, tick)
        if target_team is None or victim_team is None or attacker_team is None:
            continue
        same_round = _round_at(rounds, tick)
        if same_round is None:
            continue
        deadline = tick + round(window * tick_rate)
        followups = []
        candidates = deaths_by_victim.get(attacker, [])
        candidate_ticks = victim_ticks.get(attacker, [])
        for candidate in candidates[bisect_right(candidate_ticks, tick):]:
            later = int(candidate["tick"])
            if later > deadline:
                break
            if _round_at(rounds, later) != same_round:
                continue
            c_victim = _event_id(candidate, "user_steamid")
            c_attacker = _event_id(candidate, "attacker_steamid")
            if c_victim == attacker and c_attacker is not None:
                c_team = _team_for(ticks, c_attacker, later)
                followups.append({"tick": later, "killer": c_attacker, "team": c_team})
        if victim_team == target_team and attacker_team != target_team:
            success = any(x["killer"] == target_id for x in followups)
            events.append({"kind": "team_death", "tick": tick, "round": same_round, "traded": success,
                           "enemy_steamid": attacker, "victim_steamid": victim})
        if victim == target_id and attacker_team != target_team:
            success = any(x["team"] == target_team for x in followups)
            events.append({"kind": "target_death", "tick": tick, "round": same_round, "traded": success,
                           "enemy_steamid": attacker, "victim_steamid": victim})
    team_deaths = [e for e in events if e["kind"] == "team_death"]
    self_deaths = [e for e in events if e["kind"] == "target_death"]
    return {
        "team_kills_traded": sum(e["traded"] for e in team_deaths),
        "team_kill_opportunities": len(team_deaths),
        "target_deaths_traded": sum(e["traded"] for e in self_deaths),
        "target_death_opportunities": len(self_deaths),
        "team_kill_trade_rate": round(sum(e["traded"] for e in team_deaths) / len(team_deaths), 3) if team_deaths else None,
        "target_death_trade_rate": round(sum(e["traded"] for e in self_deaths) / len(self_deaths), 3) if self_deaths else None,
        "window_seconds": window,
        "examples": events,
    }


def analyze_features(*, target_id: int, target_name: str, tick_rate: float, rounds: list[dict[str, Any]],
                     deaths: pd.DataFrame, hurts: pd.DataFrame, fires: pd.DataFrame,
                     flash_detonations: pd.DataFrame, ticks: pd.DataFrame, config: AnalysisConfig,
                     buy_ticks: list[int] | None = None) -> dict[str, Any]:
    if not ticks.empty and {"steamid", "tick"}.issubset(ticks.columns):
        lookup = {}
        for row in ticks.to_dict(orient="records"):
            sid, tick = steamid(row.get("steamid")), number(row.get("tick"))
            if sid is not None and tick is not None:
                lookup[(sid, int(tick))] = row
        ticks = ticks.copy()
        ticks.attrs["row_lookup"] = lookup
    moments: list[dict[str, Any]] = []
    kill_count = death_count = assist_count = teamkill_count = unknown_attacker_events = 0
    for row in deaths.to_dict(orient="records"):
        tick = int(row["tick"])
        victim = _event_id(row, "user_steamid")
        attacker = _event_id(row, "attacker_steamid")
        assister = _event_id(row, "assister_steamid")
        weapon = row.get("weapon")
        if attacker == target_id and victim != target_id:
            attacker_team = _team_for(ticks, target_id, tick)
            victim_team = _team_for(ticks, victim, tick)
            if attacker_team is None or victim_team is None:
                unknown_attacker_events += 1
                kind = "unclassified_attacker_event"
            elif attacker_team == victim_team:
                teamkill_count += 1
                kind = "teamkill"
            else:
                kill_count += 1
                kind = "kill"
            moments.append(_make_moment(kind, row, target_id, rounds, tick_rate, config.pre_event_seconds,
                                        victim_steamid=victim, victim_name=str(row.get("user_name") or ""), weapon=weapon,
                                        headshot=truth(row.get("headshot", False)),
                                        team_relation=team_relation(attacker_team, victim_team)))
        if victim == target_id:
            death_count += 1
            before = _row_at(ticks, target_id, tick - 1)
            moments.append(_make_moment("death", row, target_id, rounds, tick_rate, config.pre_event_seconds,
                                        attacker_steamid=attacker, attacker_name=str(row.get("attacker_name") or ""),
                                        weapon=weapon, inventory_before=before.get("inventory"),
                                        equipment_value=number(before.get("current_equip_value"))))
        if assister == target_id:
            assist_count += 1
            moments.append(_make_moment("assist", row, target_id, rounds, tick_rate, config.pre_event_seconds,
                                        victim_steamid=victim, attacker_steamid=attacker, weapon=weapon,
                                        assisted_flash=truth(row.get("assistedflash", False))))
    moments.sort(key=lambda x: (x["tick"], x["kind"]))

    damage_to_enemies = damage_from_enemies = utility_damage = 0
    for row in hurts.to_dict(orient="records"):
        amount = int(number(row.get("dmg_health")) or 0)
        victim = _event_id(row, "user_steamid")
        attacker = _event_id(row, "attacker_steamid")
        if attacker == target_id and victim != target_id:
            damage_to_enemies += amount
            if str(row.get("weapon", "")).lower() in {"hegrenade", "inferno", "smokegrenade"}:
                utility_damage += amount
        if victim == target_id and attacker != target_id:
            damage_from_enemies += amount

    fire_samples = []
    if not fires.empty:
        for row in fires.to_dict(orient="records"):
            if _event_id(row, "user_steamid") != target_id:
                continue
            tick = int(row["tick"])
            weapon = str(row.get("weapon") or "")
            if any(x in weapon.lower() for x in ("knife", "grenade", "flashbang", "smoke", "molotov", "incgrenade", "decoy", "c4", "bayonet")):
                continue
            current = _row_at(ticks, target_id, tick)
            previous = _row_at(ticks, target_id, tick - 1)
            x0, y0 = number(previous.get("X")), number(previous.get("Y"))
            x1, y1 = number(current.get("X")), number(current.get("Y"))
            speed = math.hypot(x1 - x0, y1 - y0) * tick_rate if None not in (x0, y0, x1, y1) else None
            max_speed = number(current.get("max_speed"))
            ratio = speed / max_speed if speed is not None and max_speed and max_speed > 0 else None
            duck = number(current.get("duck_amount"))
            airborne = current.get("is_airborne")
            fire_samples.append({"tick": tick, "round": _round_at(rounds, tick), "weapon": weapon,
                                 "speed_u_s": round(speed, 2) if speed is not None else None,
                                 "max_speed_u_s": round(max_speed, 2) if max_speed is not None else None,
                                 "speed_ratio": round(ratio, 3) if ratio is not None else None,
                                 "duck_amount": duck, "is_airborne": bool(airborne) if pd.notna(airborne) else None})

    ratio_samples = [x for x in fire_samples if x["speed_ratio"] is not None]
    high_movement_fires = [x for x in ratio_samples if x["speed_ratio"] > config.stop_speed_ratio
                           and x["duck_amount"] is not None and x["duck_amount"] < config.crouch_amount_threshold
                           and x["is_airborne"] is False]
    airborne_fires = [x for x in fire_samples if x["is_airborne"] is True]

    flash_hits = []
    for row in flash_detonations.to_dict(orient="records"):
        tick = int(row["tick"])
        thrower = _event_id(row, "user_steamid")
        if ticks.empty or "steamid" not in ticks:
            continue
        current_rows = ticks[pd.to_numeric(ticks["tick"], errors="coerce") == tick]
        target_team = _team_for(ticks, target_id, tick)
        for _, current in current_rows.iterrows():
            victim = steamid(current.get("steamid"))
            before_row = _row_at(ticks, victim, tick - 1)
            before = number(before_row.get("flash_duration"))
            after = number(current.get("flash_duration"))
            if victim is None or after is None or after <= 0 or after == before:
                continue
            victim_team = number(current.get("team_num"))
            thrower_team = _team_for(ticks, thrower, tick)
            if victim == target_id:
                relation = "target_self" if thrower == target_id else f"target_{team_relation(target_team, thrower_team)}"
                flash_hits.append({"kind": "target_blinded", "tick": tick, "round": _round_at(rounds, tick),
                                   "thrower_steamid": thrower, "victim_steamid": victim,
                                   "duration_seconds": round(after, 3), "relation": relation,
                                   "meets_duration_threshold": after >= config.min_flash_duration_seconds})
            if thrower == target_id:
                relation = "self" if victim == target_id else team_relation(target_team, victim_team)
                flash_hits.append({"kind": "target_threw_flash", "tick": tick, "round": _round_at(rounds, tick),
                                   "thrower_steamid": thrower, "victim_steamid": victim,
                                   "duration_seconds": round(after, 3), "relation": relation,
                                   "meets_duration_threshold": after >= config.min_flash_duration_seconds})

    trade_data = _trades(deaths, ticks, target_id, rounds, tick_rate, config.trade_window_seconds)
    economic_snapshots = []
    for tick in sorted(set(buy_ticks or [])):
        player = _row_at(ticks, target_id, tick)
        if not player:
            continue
        team = number(player.get("team_num"))
        same_team_rows = ticks[pd.to_numeric(ticks.get("tick", pd.Series(dtype="float64")), errors="coerce").eq(tick)]
        if team is not None and "team_num" in same_team_rows:
            same_team_rows = same_team_rows[pd.to_numeric(same_team_rows["team_num"], errors="coerce").eq(team)]
        values = [number(x) for x in same_team_rows.get("current_equip_value", pd.Series(dtype="float64")).tolist()]
        values = [x for x in values if x is not None]
        player_value = number(player.get("current_equip_value"))
        economic_snapshots.append({"tick": tick, "round": _round_at(rounds, tick),
                                   "balance": number(player.get("balance")),
                                   "cash_spent_this_round": number(player.get("cash_spent_this_round")),
                                   "current_equip_value": player_value,
                                   "round_start_equip_value": number(player.get("round_start_equip_value")),
                                   "inventory": player.get("inventory"),
                                   "armor_value": number(player.get("armor_value")),
                                   "has_helmet": truth(player.get("has_helmet")) if "has_helmet" in player else None,
                                   "has_defuser": truth(player.get("has_defuser")) if "has_defuser" in player else None,
                                   "team_equip_value_sum": round(sum(values), 2) if values else None,
                                   "difference_from_team_median": round(player_value - sorted(values)[len(values)//2], 2)
                                   if player_value is not None and values else None})

    target_flash_hits = [x for x in flash_hits if x["kind"] == "target_blinded"]
    target_throw_effects = [x for x in flash_hits if x["kind"] == "target_threw_flash"]
    friendly_flash_hits = [x for x in target_throw_effects if x["relation"] == "teammate"
                           and x["meets_duration_threshold"]]
    findings = []
    if len(ratio_samples) >= config.min_pattern_samples and high_movement_fires:
        findings.append({"code": "moving_fire_candidate", "severity": "review",
                         "text": f"有 {len(high_movement_fires)} 次开火时速度超过当前武器最大速度的 {config.stop_speed_ratio:.0%}，且未检测为下蹲或腾空；建议逐段复核急停时机。",
                         "evidence_ticks": [x["tick"] for x in high_movement_fires], "sample_count": len(ratio_samples),
                         "method": "相邻 tick 的 X/Y 位移×tick_rate，再除以该 tick 的 max_speed；要求 duck_amount 和 is_airborne 均可用，仅作候选提示。"})
    if len(fire_samples) >= config.min_pattern_samples and airborne_fires:
        findings.append({"code": "airborne_fire_candidate", "severity": "review",
                         "text": f"检测到 {len(airborne_fires)} 次腾空开火；请结合武器、落点和交火情境复核。",
                         "evidence_ticks": [x["tick"] for x in airborne_fires], "sample_count": len(fire_samples),
                         "method": "依据 demo tick 字段 is_airborne 与 weapon_fire 事件对齐。"})
    if len(target_throw_effects) >= config.min_pattern_samples and friendly_flash_hits:
        findings.append({"code": "friendly_flash_candidate", "severity": "review",
                         "text": f"目标玩家的闪光效果中有 {len(friendly_flash_hits)} 次造成至少 {config.min_flash_duration_seconds:g} 秒的队友致盲；建议结合站位和投掷时机复核。",
                         "evidence_ticks": [x["tick"] for x in friendly_flash_hits], "sample_count": len(target_throw_effects),
                         "method": "在闪光爆炸 tick 比较所有玩家 flash_duration 与前一 tick，并按该 tick 的 team_num 区分队友。"})
    if trade_data.get("team_kill_opportunities", 0) >= config.min_pattern_samples and trade_data.get("team_kill_trade_rate", 1) < 0.5:
        findings.append({"code": "low_trade_rate_candidate", "severity": "review",
                         "text": f"队友阵亡后的 {trade_data['team_kill_opportunities']} 次可观察机会中，{trade_data['team_kills_traded']} 次在 {config.trade_window_seconds:g} 秒内完成补枪。",
                         "evidence_ticks": [x["tick"] for x in trade_data["examples"] if x["kind"] == "team_death" and not x["traded"]],
                         "sample_count": trade_data["team_kill_opportunities"],
                         "method": "按同一回合、目标 SteamID、敌方击杀者匹配 death 事件；该规则不调整站位与是否有安全补枪条件。"})

    return {
        "target_player": {"steamid": target_id, "name": target_name},
        "rounds": rounds,
        "moments": moments,
        "metrics": {"kills": kill_count, "teamkills": teamkill_count,
                    "unclassified_attacker_events": unknown_attacker_events,
                    "deaths": death_count, "assists": assist_count,
                    "damage_dealt": damage_to_enemies, "damage_received": damage_from_enemies,
                    "utility_damage_dealt": utility_damage,
                    "weapon_fire_events": len(fire_samples),
                    "movement_fire_samples": len(ratio_samples),
                    "high_movement_fire_candidates": len(high_movement_fires),
                    "airborne_fire_events": len(airborne_fires),
                    "team_kill_opportunities": trade_data.get("team_kill_opportunities", 0),
                    "team_kills_traded": trade_data.get("team_kills_traded", 0),
                    "target_death_opportunities": trade_data.get("target_death_opportunities", 0),
                    "target_deaths_traded": trade_data.get("target_deaths_traded", 0)},
        "economy_snapshots": economic_snapshots,
        "flash_summary": {"target_blinded_events": len(target_flash_hits),
                          "target_blinded_events_ge_threshold": sum(x["meets_duration_threshold"] for x in target_flash_hits),
                          "teammates_blinded_ge_threshold": len(friendly_flash_hits),
                          "enemies_blinded_ge_threshold": sum(x["relation"] == "enemy" and x["meets_duration_threshold"] for x in target_throw_effects)},
        "trade_analysis": trade_data,
        "flash_effects_on_target": flash_hits,
        "movement_fire_samples": fire_samples,
        "findings": findings,
        "thresholds": {"trade_window_seconds": config.trade_window_seconds,
                       "stop_speed_ratio": config.stop_speed_ratio,
                       "crouch_amount_threshold": config.crouch_amount_threshold,
                       "min_flash_duration_seconds": config.min_flash_duration_seconds,
                       "min_pattern_samples": config.min_pattern_samples},
    }
