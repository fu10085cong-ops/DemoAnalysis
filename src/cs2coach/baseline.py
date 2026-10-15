"""职业基准：用与分析用户完全相同的流程，统计职业比赛中每人每场的枪法数值（docs/product.md "判定标准"）。

样本清单在 baselines/pro_demos.toml，由 scripts/build_baseline.py 生成整场、按武器及连射分段基准，随包分发。
报告中必须注明样本来源和大小。
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

from cs2coach.features.labels import WEAPON_LABELS
from cs2coach.features.models import MatchMoments, Moment

BASELINE_FILE = Path(__file__).resolve().parent / "baselines" / "pro_sample.json"
DEMO_MANIFEST = BASELINE_FILE.with_name("pro_demos.toml")
EXCLUDED_TAGS = {"after_round_end", "teamkill", "victim_is_bot", "world_damage"}

# 数值越小越好的指标
LOWER_IS_BETTER = {"time_to_damage_ms", "crosshair_placement_deg"}
METRICS = [
    "time_to_damage_ms", "crosshair_placement_deg", "counter_strafing_pct",
    "spray_accuracy_pct", "spotted_accuracy_pct", "headshot_accuracy_pct",
]


def build_baseline(values: dict[str, list[float]], source: str) -> dict[str, Any]:
    out: dict[str, Any] = {"source": source, "metrics": {}}
    for m in METRICS:
        v = sorted(x for x in values.get(m, []) if x is not None)
        if not v:
            continue
        q = statistics.quantiles(v, n=4) if len(v) >= 2 else [v[0], v[0], v[0]]
        out["metrics"][m] = {"median": round(statistics.median(v), 2), "p25": round(q[0], 2),
                             "p75": round(q[2], 2), "n": len(v), "values": [round(x, 2) for x in v]}
    return out


def load_baseline(path: Path = BASELINE_FILE) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def is_pro_player(steamid: int, baseline: dict[str, Any] | None, cfg: dict[str, Any]) -> bool:
    """职业名单来自实际参与基准的选手或手工补充的 SteamID。"""
    players = baseline.get("players", []) if baseline is not None else []
    return (any(player.get("steamid") == steamid for player in players)
            or steamid in cfg["training"]["extra_pro_steamids"])


def pro_training_note(steamid: int, baseline: dict[str, Any] | None,
                      cfg: dict[str, Any]) -> str | None:
    """单场与后续多场共用的职业选手训练说明。"""
    if not is_pro_player(steamid, baseline, cfg):
        return None
    in_sample = any(player.get("steamid") == steamid
                    for player in (baseline.get("players", []) if baseline is not None else []))
    advice = ("训练建议面向职业水平：聚焦细节、对局决策和针对性的情境练习，"
              "不推荐基础的瞄准、压枪练习图。")
    if in_sample:
        return ("目标玩家是职业选手，本人就在职业样本中。" + advice +
                "与职业样本的比较，反映的是他和其他职业选手的差距。")
    return ("目标玩家是职业选手，本人不在职业样本中。" + advice +
            "与职业样本的比较仅供参考，不能当作与其他职业选手的直接差距。")


def compare(metric: str, value: float | None, baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    """与职业样本对比：职业中位数、四分位，以及"优于职业样本中多少比例的人次"（考虑指标方向）。"""
    if value is None or baseline is None or metric not in baseline.get("metrics", {}):
        return None
    b = baseline["metrics"][metric]
    vals = b["values"]
    if metric in LOWER_IS_BETTER:
        better = sum(value < x for x in vals) + 0.5 * sum(value == x for x in vals)
    else:
        better = sum(value > x for x in vals) + 0.5 * sum(value == x for x in vals)
    return {"pro_median": b["median"], "pro_p25": b["p25"], "pro_p75": b["p75"], "pro_n": b["n"],
            "better_than_pro_pct": round(100.0 * better / len(vals))}


def engagement_weapon(m: Moment) -> str | None:
    """交火第一枪的武器；没有开枪时反查手持武器的显示名。基准与问题检测共用。"""
    if m.engagement is not None and m.engagement.shots:
        return min(m.engagement.shots, key=lambda s: s.tick).weapon
    reverse = {display: name for name, display in WEAPON_LABELS.items()}
    return reverse.get(m.context.own_weapon)


def iter_engagements(mms: list[MatchMoments]):
    """职业逐次交火与压枪的共用样本范围。"""
    for mm in mms:
        for m in mm.moments:
            if m.kind in ("kill", "death") and m.engagement is not None and not EXCLUDED_TAGS.intersection(m.tags):
                yield m


def _distribution(values: list[float | None]) -> dict[str, Any]:
    v = [x for x in values if x is not None and math.isfinite(x)]
    if not v:
        return {"p50": None, "p75": None, "p90": None, "n": 0}
    q = np.percentile(v, [50, 75, 90])
    return {**{key: round(float(x), 2) for key, x in zip(("p50", "p75", "p90"), q)}, "n": len(v)}


def weapon_stats(mms: list[MatchMoments], cfg: dict) -> dict:
    """按武器统计交火分布；分位数用线性插值，TTD 排除等待、烟雾和被闪白。"""
    groups: dict[str, list[Moment]] = {}
    for m in iter_engagements(mms):
        w = engagement_weapon(m)
        if w is not None:
            groups.setdefault(w, []).append(m)
    out = {}
    for w, ms in groups.items():
        if len(ms) < cfg["baseline"]["min_engagements"]:
            continue
        reliable = [m.engagement for m in ms if m.engagement.visibility_note not in ("smoke", "blind")]
        ttd = [e.time_to_damage_s * 1000 for e in reliable if e.time_to_damage_s is not None
               and e.time_to_damage_s < cfg["aim_stats"]["time_to_damage_max_seconds"]]
        stopped = [m.engagement.first_shot_stopped for m in ms if m.engagement.first_shot_stopped is not None]
        hit = [m.engagement.first_shot_hit for m in ms if m.engagement.first_shot_hit is not None]
        out[w] = {
            "engagements": len(ms), "time_to_damage_ms": _distribution(ttd),
            "crosshair_placement_deg": _distribution([e.crosshair_placement_deg for e in reliable]),
            "first_shot_stopped_pct": round(100 * sum(stopped) / len(stopped), 2) if stopped else None,
            "first_shot_stopped_n": len(stopped),
            "first_shot_hit_pct": round(100 * sum(hit) / len(hit), 2) if hit else None,
            "first_shot_hit_n": len(hit),
            "distance_m": _distribution([m.context.distance for m in ms]),
        }
    return out


def spray_stats(mms: list[MatchMoments], cfg: dict) -> dict:
    """按每发子弹实际使用的自动武器、recoil_index 分段统计命中率。"""
    starts = cfg["baseline"]["spray_segment_starts"]
    counts: dict[str, list[list[int]]] = {}
    for m in iter_engagements(mms):
        if engagement_weapon(m) is None:
            continue
        for s in m.engagement.shots:
            if s.weapon not in cfg["baseline"]["automatic_weapons"] or s.recoil_index is None:
                continue
            if not math.isfinite(s.recoil_index):
                continue
            indices = [i for i, start in enumerate(starts) if s.recoil_index >= start]
            if not indices:
                continue
            i = indices[-1]
            pairs = counts.setdefault(s.weapon, [[0, 0] for _ in starts])
            pairs[i][0] += 1
            pairs[i][1] += int(s.hit)
    return {w: [{"from": start, "to": starts[i + 1] - 1 if i + 1 < len(starts) else None,
                 "shots": n, "hit_pct": round(100 * hit / n, 2) if n else None}
                for i, (start, (n, hit)) in enumerate(zip(starts, pairs))]
            for w, pairs in counts.items()}


def weapon_threshold(baseline: dict | None, weapon: str | None, metric: str, quantile: str) -> float | None:
    """取同武器的职业阈值；兼容旧版基准和缺少样本的武器。"""
    if baseline is None:
        return None
    value = baseline.get("weapons", {}).get(weapon, {}).get(metric, {}).get(quantile)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def spray_segments(baseline: dict | None, weapon: str | None) -> list[dict]:
    """职业同武器的连射发数段，旧基准或缺失武器返回空列表。"""
    return baseline.get("spray", {}).get(weapon, []) if baseline is not None else []


def problem_rates(mms: list[MatchMoments], baseline: dict, cfg: dict) -> dict:
    """用本次新基准检测职业选手，保留停用规则的校准频率，但排除整场目标。"""
    # 检测器依赖基准读取接口，因此在函数内导入，避免模块循环依赖。
    from cs2coach.features.problems import DETECTED_PROBLEMS, detect_problems

    calibration = deepcopy(cfg)
    calibration["problems"]["disabled"] = []
    calibration["problems"]["spray_match_enabled"] = True
    hits, players = Counter(), Counter()
    for mm in mms:
        report = detect_problems(mm, baseline, calibration)
        counts = Counter(h.problem for h in report.hits if h.target != "match")
        hits.update(counts)
        players.update(counts.keys())
    rounds = sum(mm.rounds_played for mm in mms)
    return {pid: {"hits": hits[pid], "rounds": rounds,
                  "per_10_rounds": round(10 * hits[pid] / rounds, 2) if rounds > 0 else 0.0,
                  "player_share_pct": round(100 * players[pid] / len(mms), 1) if mms else 0.0}
            for pid in DETECTED_PROBLEMS}
