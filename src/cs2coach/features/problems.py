"""问题目录的确定性前提条件；命中只表示可供审查，不代表一定是失误。"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Literal

from pydantic import BaseModel

from cs2coach.baseline import EXCLUDED_TAGS, engagement_weapon, spray_segments, spray_stats, weapon_threshold
from cs2coach.features.labels import weapon_label
from cs2coach.features.models import MatchMoments


class ProblemSpec(BaseModel):
    id: str
    label: str
    category: Literal["aim", "duel", "utility_team", "weapon", "highlight"]
    applies_to: list[Literal["kill", "death", "assist", "round", "match"]]
    reliability: Literal["high", "medium", "low"]


class ProblemHit(BaseModel):
    problem: str
    target: str
    weapon: str | None
    evidence: dict[str, float | int | str | bool | None]
    thresholds: dict[str, float | int | str | None]


class ProblemReport(BaseModel):
    hits: list[ProblemHit]
    disabled: list[str]
    skipped: dict[str, int]
    baseline_source: str | None


class FrequencyRow(BaseModel):
    problem: str
    label: str
    hits: int
    per_10_rounds: float
    pro_per_10_rounds: float | None
    ratio: float | None
    prominent: bool
    expected_hits: float | None = None
    excess_hits: float | None = None
    chance: float | None = None
    needs_more_matches: bool = False


# 登记目录的全部编号；仅下方 detect_problems 中明确实现的规则参与检测。
_SPECS = [
    ("slow_reaction", "出枪慢", "aim", ["kill", "death"], "medium"),
    ("poor_crosshair_placement", "准星没提前放好", "aim", ["kill", "death"], "medium"),
    ("shot_while_moving", "开枪时没停稳", "aim", ["death"], "high"),
    ("peeked_into_held_angle", "主动拉出撞上对手架点", "duel", ["death"], "medium"),
    ("dry_peek_with_utility", "身上有道具却直接拉出", "duel", ["death"], "medium"),
    ("overpeek_in_advantage", "人数优势时冒进", "duel", ["death"], "medium"),
    ("died_without_seeing", "没看见对手就被打死", "duel", ["death"], "low"),
    ("turned_into_flash", "被对手闪中后被击杀", "duel", ["death"], "medium"),
    ("died_while_reloading", "换弹时被打死", "duel", ["death"], "high"),
    ("missed_trade", "漏补枪", "utility_team", ["round"], "medium"),
    ("post_plant_utility_unused", "下包后带着火没用", "utility_team", ["death"], "high"),
    ("good_trade", "及时补枪", "highlight", ["kill"], "high"),
    ("flash_assist", "闪光助攻", "highlight", ["assist"], "high"),
    ("won_duel_seen_first", "先发现、先开枪赢下对枪", "highlight", ["kill"], "medium"),
    ("spray_control", "压枪差", "weapon", ["kill", "death", "match"], "medium"),
    ("spray_while_moving", "扫射时在移动", "weapon", ["death"], "high"),
    ("overspray_at_range", "远距离长扫", "weapon", ["death"], "low"),
    ("weapon_not_ready", "拉栓期间交火", "weapon", ["death"], "high"),
    ("awp_unscoped_shot", "没开镜就开枪", "weapon", ["death"], "high"),
    ("awp_moving_shot", "移动中开枪", "weapon", ["death"], "high"),
    ("awp_repeek_same_angle", "开枪后在同一位置重复拉出", "weapon", ["death"], "low"),
    ("awp_close_range_duel", "拿狙近距离对枪", "weapon", ["death"], "low"),
    ("pistol_spam", "连点过快，准度还没恢复", "weapon", ["death"], "low"),
    ("deagle_moving_shot", "沙鹰移动开枪", "weapon", ["death"], "high"),
    ("glock_long_range", "用 Glock 远距离对枪", "weapon", ["death"], "medium"),
    ("other", "AI 判断，无程序依据", "duel", ["kill", "death", "assist", "round", "match"], "low"),
]
PROBLEMS: dict[str, ProblemSpec] = {pid: ProblemSpec(id=pid, label=label, category=cat, applies_to=kinds, reliability=rel)
            for pid, label, cat, kinds, rel in _SPECS}
# 用户确认：频率只统计实际实现的规则，待开发、仅登记及 other 不作零频率比较。
DETECTED_PROBLEMS = (
    "slow_reaction", "poor_crosshair_placement", "shot_while_moving",
    "peeked_into_held_angle", "dry_peek_with_utility", "overpeek_in_advantage",
    "died_without_seeing", "turned_into_flash", "died_while_reloading", "missed_trade", "post_plant_utility_unused",
    "weapon_not_ready", "awp_unscoped_shot", "deagle_moving_shot", "glock_long_range",
    "spray_control", "spray_while_moving", "good_trade", "flash_assist", "won_duel_seen_first",
)
CONTEXT_ONLY: dict[str, str] = {
    "unused_utility": "单独带道具阵亡与战绩正相关，不能单独定为问题。",
    "isolated_death": "离队友远、孤立阵亡尚未用回合胜负验证。",
    "opponent_saw_first": "零和背景，需要结合具体情境。",
    "opening_death": "零和背景，需要结合具体情境。",
    "man_advantage": "人数优势时阵亡本身只作背景。",
    "friendly_flash": "被队友闪中不是本人的问题。",
    "turned_into_flash": "路人并不比职业多，主要反映对手的道具水平。",
    "weapon_not_ready": "路人与职业一样多，多数是被迫的。",
}


def _finite(value) -> bool:
    return isinstance(value, (float, int)) and math.isfinite(value)


def _json(value) -> str:
    """证据结构为标量字典；多发子弹、多个道具用 JSON 字符串完整保留。"""
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _segment(segments, recoil):
    # recoil_index 是浮点数；段的上限由下一段起点决定，例如 2.5 仍属于 1–2 段。
    candidates = [s for s in segments if _finite(s.get("from")) and recoil >= s["from"]
                  and (s.get("to") is None or recoil < s["to"] + 1)]
    return max(candidates, key=lambda s: s["from"]) if candidates else None


def detect_problems(mm: MatchMoments, baseline: dict | None, cfg: dict) -> ProblemReport:
    """检测任务 005 的明确规则；缺失值不推断，缺阈值计入 skipped。"""
    p = cfg["problems"]
    disabled = list(p["disabled"])
    hits: list[ProblemHit] = []
    skipped: Counter = Counter()
    automatic = cfg["baseline"]["automatic_weapons"]

    def exclusions(moment) -> set[str]:
        if moment is None:
            return set()
        engagement = moment.engagement
        active = set()
        if moment.through_smoke:
            active.add("through_smoke")
        if moment.wallbang:
            active.add("wallbang")
        if engagement is not None:
            if engagement.visibility_note == "smoke":
                active.add("first_seen_through_smoke")
            if engagement.player_blind is not None:
                active.add("player_blinded_in_fight")
            if (engagement.opponent_blind is not None and
                    engagement.opponent_blind.thrower_role in ("self", "teammate")):
                active.add("opponent_blinded_by_own_side")
            if (engagement.shots_before_seen is not None and
                    engagement.shots_before_seen >= p["max_shots_before_seen"]):
                active.add("fired_before_seen")
            if engagement.airborne_near_seen is True:
                active.add("airborne_near_seen")
        if moment.kind == "death" and moment.weapon in ("hegrenade", "inferno", "molotov", "incgrenade"):
            active.add("killed_by_utility")
        return active

    def missing(pid):
        if pid not in disabled:
            skipped[pid] += 1

    def emit(pid, target, weapon, evidence, thresholds=None, moment=None):
        if pid not in disabled:
            excluded = exclusions(moment)
            reasons = [condition for condition, ids in p["exclusions"].items()
                       if condition in excluded and pid in ids]
            if reasons:
                for reason in reasons:
                    skipped[f"{pid}/{reason}"] += 1
                return
            hits.append(ProblemHit(problem=pid, target=target, weapon=weapon,
                                   evidence={"weapon": weapon, **evidence}, thresholds=thresholds or {}))

    def threshold(pid, weapon, metric, quantile):
        if pid in disabled:
            return None
        value = weapon_threshold(baseline, weapon, metric, quantile)
        if value is None:
            missing(pid)
        return value

    for m in mm.moments:
        if EXCLUDED_TAGS.intersection(m.tags):
            continue
        w, e, d, c = engagement_weapon(m), m.engagement, m.death, m.context
        audit = {"round": m.round, "tick": m.tick, "kind": m.kind}

        def hit(pid, evidence, thresholds=None, weapon=None):
            emit(pid, m.id, weapon or w, audit | evidence, thresholds, m)

        if m.kind in ("kill", "death"):
            if e is None:
                for pid in ("slow_reaction", "poor_crosshair_placement"):
                    missing(pid)
            else:
                ttd, opp = e.time_to_damage_s, e.opponent_time_to_damage_s
                cap = cfg["aim_stats"]["time_to_damage_max_seconds"]
                if e.visibility_note not in ("smoke", "blind") and e.player_seen_while_blind is not True:
                    # 032：只比较"看见 → 开枪"；慢在"开枪 → 命中"属于没打中，不算反应慢
                    react = e.seen_to_first_shot_s
                    if not _finite(ttd) or not _finite(react) or (m.kind == "death" and not _finite(opp)):
                        missing("slow_reaction")
                    elif ttd < cap:
                        limit = threshold("slow_reaction", w, "seen_to_first_shot_ms", "p75")
                        if limit is not None and react * 1000 > limit * (1 + p["slow_reaction_margin_ratio"]):
                            if m.kind == "kill" and not _finite(opp):
                                # 035：击杀了根本没打到你的对手，出枪慢不值得提醒（第三轮审计）
                                skipped["slow_reaction/opponent_no_damage"] += 1
                            elif m.kind == "kill" or opp < ttd:
                                hit("slow_reaction", {"seen_to_first_shot_ms": react * 1000,
                                    "first_shot_to_damage_ms": (e.first_shot_to_damage_s * 1000
                                                                if _finite(e.first_shot_to_damage_s) else None),
                                    "ttd_ms": ttd * 1000,
                                    "opponent_ttd_ms": opp * 1000 if _finite(opp) else None,
                                    "visibility_note": e.visibility_note,
                                    "player_seen_while_blind": e.player_seen_while_blind},
                                    {"seen_to_first_shot_ms": limit, "margin_ratio": p["slow_reaction_margin_ratio"],
                                     "source": "pro_p75", "ttd_max_seconds": cap,
                                     "ttd_max_source": "config"})
                cp = e.crosshair_initial_offset_m
                if not _finite(cp):
                    missing("poor_crosshair_placement")
                else:
                    limit = threshold("poor_crosshair_placement", w, "crosshair_initial_offset_m", "p75")
                    if limit is not None and cp > limit * (1 + p["crosshair_margin_ratio"]):
                        # 035：近身遭遇的准星偏差不值得提醒（第三轮审计）；距离缺失时照常判
                        if c is not None and _finite(c.distance) and c.distance < p["crosshair_min_distance_m"]:
                            skipped["poor_crosshair_placement/close_range"] += 1
                        else:
                            hit("poor_crosshair_placement", {
                                "crosshair_initial_offset_m": cp,
                                "crosshair_initial_deg": e.crosshair_initial_deg,
                                "crosshair_initial_yaw_deg": e.crosshair_initial_yaw_deg,
                                "crosshair_initial_pitch_deg": e.crosshair_initial_pitch_deg,
                                "distance_m": c.distance if c is not None else None},
                                {"crosshair_initial_offset_m": limit, "margin_ratio": p["crosshair_margin_ratio"],
                                 "source": "pro_p75"})

                if w in automatic and (not _finite(c.distance) or c.distance <= p["spray_max_distance_m"]):
                    eligible = [s for s in e.shots if s.weapon == w and _finite(s.recoil_index)
                                and s.recoil_index >= p["spray_late_recoil_index"]]
                    late = [s for s in eligible if s.seen_opponent is True]
                    excluded_unseen = len(eligible) - len(late)
                    if len(late) >= p["spray_min_late_shots"]:
                        segments = spray_segments(baseline, w)
                        refs = [_segment(segments, s.recoil_index) for s in late]
                        if any(s is None or not _finite(s.get("hit_pct")) for s in refs):
                            missing("spray_control")
                        else:
                            expected = sum(s["hit_pct"] for s in refs) / len(late)
                            actual = 100 * sum(s.hit for s in late) / len(late)
                            if actual < expected - p["spray_margin_pct"]:
                                hit("spray_control", {"scope": "engagement", "shots": len(late),
                                    "hits": sum(s.hit for s in late), "hit_pct": actual,
                                    "excluded_unseen_shots": excluded_unseen,
                                    "recoil_indices": _json([s.recoil_index for s in late]),
                                    "shot_hits": _json([s.hit for s in late])},
                                    {"expected_hit_pct": expected, "source": "pro_segments_weighted",
                                     "pro_hit_pcts": _json([s["hit_pct"] for s in refs]),
                                     "margin_pct": p["spray_margin_pct"], "min_shots": p["spray_min_late_shots"],
                                    "min_recoil_index": p["spray_late_recoil_index"],
                                    "max_distance_m": p["spray_max_distance_m"], "limits_source": "config"})

        if m.kind == "death":
            if e is not None:
                if e.shots and (e.first_seen_shot_stopped is None or e.first_seen_shot_hit is None):
                    missing("shot_while_moving")
                if e.contact_type is None:
                    missing("peeked_into_held_angle")
                    missing("dry_peek_with_utility")
                    if "man_advantage" in m.tags:
                        missing("overpeek_in_advantage")
                first_seen = min((shot for shot in e.shots if shot.seen_opponent is True),
                                 key=lambda shot: shot.tick, default=None)
                shot_weapon = first_seen.weapon if first_seen is not None else None
                speed_limit = p["moving_shot_thresholds"].get(shot_weapon)
                if (e.first_seen_shot_stopped is False and e.first_seen_shot_hit is False and
                        speed_limit is not None and first_seen is not None and _finite(first_seen.speed_ratio) and
                        first_seen.speed_ratio >= speed_limit):
                    hit("shot_while_moving", {"first_seen_shot_stopped": False, "first_seen_shot_hit": False,
                        "first_seen_shot_speed_ratio": first_seen.speed_ratio},
                        {"min_speed_ratio": speed_limit, "weapon_name": weapon_label(shot_weapon), "source": "config"},
                        weapon=shot_weapon)
                if e.contact_type == "peek":
                    hit("peeked_into_held_angle", {"contact_type": e.contact_type})
                    if d is None:
                        missing("dry_peek_with_utility")
                    if "man_advantage" in m.tags and (m.trade is None or m.trade.traded is None):
                        missing("overpeek_in_advantage")
                    if d is not None and set(p["dry_peek_grenades"]).intersection(d.unused_grenades) and e.opponent_blind is None:
                        hit("dry_peek_with_utility", {"contact_type": e.contact_type,
                            "unused_grenades": _json(d.unused_grenades), "opponent_blind": None})
                    # CT 已下包时必须去拆包，拉出是被迫的
                    forced_retake = c.side == "CT" and c.bomb_planted
                    # 035：T 方未下包的开局阶段拉出是正常进点（第三轮审计），CT 不受影响
                    t_early_round = (c.side == "T" and not c.bomb_planted and
                                     c.round_time_s < p["overpeek_t_min_round_time_s"])
                    if ("man_advantage" in m.tags and m.trade is not None and m.trade.traded is False
                            and not forced_retake and not t_early_round):
                        hit("overpeek_in_advantage", {"man_advantage": True, "contact_type": e.contact_type, "traded": False})
                if (shot_weapon == "deagle" and e.first_seen_shot_stopped is False and
                        first_seen is not None and _finite(first_seen.speed_ratio) and
                        first_seen.speed_ratio >= speed_limit):
                    hit("deagle_moving_shot", {"first_seen_shot_stopped": False,
                        "first_seen_shot_speed_ratio": first_seen.speed_ratio},
                        {"min_speed_ratio": speed_limit, "weapon_name": weapon_label(shot_weapon), "source": "config"},
                        weapon=shot_weapon)
                if w == "awp" and e.shots:
                    first = min(e.shots, key=lambda s: s.tick)
                    if first.scoped is False and not _finite(c.distance):
                        missing("awp_unscoped_shot")
                    elif first.scoped is False and c.distance > p["awp_noscope_max_distance_m"]:
                        hit("awp_unscoped_shot", {"first_shot_tick": first.tick, "scoped": first.scoped,
                            "distance_m": c.distance}, {"distance_m": p["awp_noscope_max_distance_m"], "source": "config"})
                if w in automatic:
                    moving = [s for s in e.shots if s.weapon == w and _finite(s.recoil_index)
                              and s.recoil_index >= p["spray_moving_min_recoil_index"]
                              and s.seen_opponent is True]
                    if len(moving) >= p["spray_moving_min_shots"]:
                        if any(s.stopped is None for s in moving):
                            missing("spray_while_moving")
                        else:
                            ratio = sum(s.stopped is False for s in moving) / len(moving)
                            if ratio > p["spray_moving_ratio"]:
                                hit("spray_while_moving", {"shots": len(moving), "moving_ratio": ratio,
                                    "moving_shots": sum(s.stopped is False for s in moving),
                                    "recoil_indices": _json([s.recoil_index for s in moving]),
                                    "shot_stopped": _json([s.stopped for s in moving])},
                                    {"moving_ratio": p["spray_moving_ratio"], "min_shots": p["spray_moving_min_shots"],
                                     "min_recoil_index": p["spray_moving_min_recoil_index"], "source": "config"})

            # 只算"对手在屏幕外"：双方有视线但对手不在本人屏幕内（侧面或背后）。完全没有几何视线却被打死
            # 多半是几何误差，不判（2026-10-17 审计：189/197 属于前者；游戏内确认 3/3）
            if ("died_without_seeing" in m.tags and d is not None and d.blinded is False
                    and e is not None and e.opponent_off_screen is True):
                hit("died_without_seeing", {"died_without_seeing": True, "opponent_off_screen": True,
                    "wallbang": m.wallbang, "through_smoke": m.through_smoke, "blinded": d.blinded})
            blind = e.player_blind if e is not None else None
            if blind is not None:
                if blind.blinded_at_event and blind.thrower_role is None:
                    missing("turned_into_flash")
                if blind.blinded_at_event and blind.thrower_role in ("opponent", "enemy"):
                    hit("turned_into_flash", {"blind_source": "engagement.player_blind",
                        "blinded_at_event": blind.blinded_at_event, "thrower_role": blind.thrower_role})
            elif d is not None and d.blinded and d.blinded_by_teammate is False:
                hit("turned_into_flash", {"blind_source": "death", "player_blind": None,
                    "blinded": d.blinded, "blinded_by_teammate": d.blinded_by_teammate})
            elif d is not None and d.blinded and d.blinded_by_teammate is None:
                missing("turned_into_flash")
            if d is not None and d.reloading_at_death:
                if d.ammo_before_reload is None:
                    missing("died_while_reloading")
                elif d.ammo_before_reload >= p["reload_min_ammo_left"]:
                    hit("died_while_reloading", {"reloading_at_death": True,
                        "ammo_before_reload": d.ammo_before_reload, "reload_started_s": d.reload_started_s},
                        {"min_ammo_left": p["reload_min_ammo_left"], "source": "config"})
            if (d is not None and c.side == "T" and c.bomb_planted and {"molotov", "incgrenade"}.intersection(d.unused_grenades)
                    and _finite(c.seconds_since_plant) and c.seconds_since_plant >= p["post_plant_min_alive_seconds"]):
                hit("post_plant_utility_unused", {"side": c.side, "bomb_planted": c.bomb_planted,
                    "unused_grenades": _json(d.unused_grenades), "seconds_since_plant": c.seconds_since_plant},
                    {"min_alive_seconds": p["post_plant_min_alive_seconds"], "source": "config"})
            if "weapon_not_ready" in m.tags:
                hit("weapon_not_ready", {"weapon_not_ready": True})
            if w == "glock":
                if not _finite(c.distance):
                    missing("glock_long_range")
                else:
                    limit = threshold("glock_long_range", w, "distance_m", "p90")
                    # m.weapon 是击杀者的武器：Glock 对 Glock 远距离对枪是对称的，不算失误
                    if limit is not None and c.distance > limit and m.weapon in p["glock_long_range_opponent_weapons"]:
                        hit("glock_long_range", {"distance_m": c.distance}, {"distance_m": limit, "source": "pro_p90"})

        if m.kind == "kill":
            if "trade_kill" in m.tags:
                hit("good_trade", {"trade_kill": True})
            if ({"saw_opponent_first", "shot_first"}.issubset(m.tags) and e is not None and
                    _finite(e.seen_first_lead_s) and _finite(e.first_shot_lead_s) and
                    e.seen_first_lead_s >= p["highlight_min_lead_s"] and
                    e.first_shot_lead_s >= p["highlight_min_lead_s"]):
                hit("won_duel_seen_first", {"saw_opponent_first": True, "shot_first": True,
                    "seen_first_lead_s": e.seen_first_lead_s, "first_shot_lead_s": e.first_shot_lead_s},
                    {"min_lead_s": p["highlight_min_lead_s"], "source": "config"})
        if m.kind == "assist" and m.assist is not None and m.assist.flash_assist:
            hit("flash_assist", {"flash_assist": True})

    for record in mm.missed_trades:
        if p["missed_trade_exclude_traded_by_other"] and record.killer_traded_by_other:
            if "missed_trade" not in disabled:
                skipped["missed_trade/killer_traded_by_other"] += 1
            continue
        # 035：队友阵亡后只剩自己一个人，补枪无从谈起（第三轮审计）；alive_own 缺失时照常判
        if record.alive_own == 1:
            skipped["missed_trade/last_alive"] += 1
            continue
        if record.killer_in_view_s is None:
            missing("missed_trade")
            continue
        if record.killer_in_view_s < p["missed_trade_min_view_seconds"]:
            skipped["missed_trade/killer_in_view_s"] += 1
            continue
        if (record.killer_first_in_view_s is None or
                record.killer_first_in_view_s > cfg["moments"]["trade_window_seconds"] - p["missed_trade_min_react_seconds"]):
            skipped["missed_trade/killer_first_in_view_s"] += 1
            continue
        if record.reloading_at_death:
            skipped["missed_trade/reloading_at_death"] += 1
            continue
        if record.other_fight_s is not None:
            skipped["missed_trade/other_fight_s"] += 1
            continue
        emit("missed_trade", f"R{record.round}", None, record.model_dump())

    for w, segments in (spray_stats([mm], cfg) if p["spray_match_enabled"] else {}).items():
        refs = spray_segments(baseline, w)
        for s in segments:
            if s["shots"] < p["spray_min_segment_shots"]:
                continue
            ref = next((r for r in refs if r.get("from") == s["from"] and r.get("to") == s["to"]), None)
            if ref is None or not _finite(ref.get("hit_pct")):
                missing("spray_control")
            elif s["hit_pct"] < ref["hit_pct"] - p["spray_margin_pct"]:
                emit("spray_control", "match", w, {"scope": "match", "segment": f'{s["from"]}–{s["to"] if s["to"] is not None else "+"}',
                    "from": s["from"], "to": s["to"], "shots": s["shots"], "hit_pct": s["hit_pct"]},
                    {"expected_hit_pct": ref["hit_pct"], "source": "pro_segment",
                     "margin_pct": p["spray_margin_pct"], "min_shots": p["spray_min_segment_shots"], "limits_source": "config"})

    return ProblemReport(hits=hits, disabled=disabled, skipped=dict(sorted(skipped.items())),
                         baseline_source=baseline.get("source") if baseline is not None else None)


def _poisson_tail(count: int, expected: float) -> float:
    """泊松分布右尾 P(X >= count)，用对数概率避免大参数下阶乘溢出。"""
    if count <= 0:
        return 1.0
    if expected == 0:
        return 0.0
    log_expected = math.log(expected)
    if count <= expected:
        cdf = math.fsum(math.exp(-expected + i * log_expected - math.lgamma(i + 1))
                        for i in range(count))
        return max(0.0, min(1.0, 1.0 - cdf))
    term = math.exp(-expected + count * log_expected - math.lgamma(count + 1))
    total = term
    i = count
    while term > 1e-15 * total:
        i += 1
        term *= expected / i
        total += term
    return min(1.0, total)


def problem_frequency(report: ProblemReport, mm: MatchMoments, baseline: dict | None,
                      cfg: dict) -> list[FrequencyRow]:
    """每十回合的问题频率与职业对比；不含整场目标、停用项和亮点。"""
    counts = Counter(h.problem for h in report.hits if h.target != "match")
    disabled = set(report.disabled) | set(cfg["problems"]["disabled"])
    return frequency_from_counts(counts, mm.rounds_played, disabled, baseline, cfg)


def frequency_from_counts(counts: Counter, rounds: int, disabled: set[str],
                          baseline: dict | None, cfg: dict) -> list[FrequencyRow]:
    """单场与多场共用的频率、职业预期及突出判断。"""
    rates = baseline.get("problem_rates", {}) if baseline is not None else {}
    rows = []
    for pid in DETECTED_PROBLEMS:
        spec = PROBLEMS[pid]
        if pid in disabled or spec.category == "highlight":
            continue
        hits = counts.get(pid, 0)
        frequency = 10 * hits / rounds if rounds > 0 else 0.0
        rate = rates.get(pid, {})
        pro_hits, pro_rounds = rate.get("hits"), rate.get("rounds")
        valid_rate = (_finite(pro_hits) and _finite(pro_rounds) and
                      pro_hits >= 0 and pro_rounds > 0)
        pro = 10 * pro_hits / pro_rounds if valid_rate else None
        expected = rounds * pro_hits / pro_rounds if valid_rate else None
        excess = hits - expected if expected is not None else None
        chance = (_poisson_tail(hits, expected) if expected is not None and expected > 0
                  and rounds > 0 else None)
        ratio = frequency / pro if pro is not None and pro > 0 else None
        prominent = (chance is not None and chance < cfg["problems"]["frequency_max_chance"]
                     and excess >= cfg["problems"]["frequency_min_excess"])
        rows.append(FrequencyRow(problem=pid, label=spec.label, hits=hits, per_10_rounds=frequency,
            pro_per_10_rounds=pro, ratio=ratio, prominent=prominent,
            expected_hits=expected, excess_hits=excess, chance=chance,
            needs_more_matches=excess is not None and excess > 0 and not prominent))
    return sorted(rows, key=lambda row: (not row.prominent,
                  -row.excess_hits if row.excess_hits is not None else math.inf))
