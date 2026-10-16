"""多场 AI 摘要：把聚合数据和可追溯的代表性命中交给模型。"""

from __future__ import annotations

import math
import json
from collections import Counter

from cs2coach.baseline import compare, pro_training_note
from cs2coach.features.problems import PROBLEMS, ProblemHit
from cs2coach.report.evidence import evidence_sentence
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.summary.render import summary_frequency_table, summary_match_table
from cs2coach.tools.digest import METRIC_LABELS, TypedNumbers
from cs2coach.tools.drills import drill_guidance


def _numeric_leaves(value) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)] if math.isfinite(value) else []
    if isinstance(value, str) and value.lstrip().startswith(("[", "{")):
        try:
            return _numeric_leaves(json.loads(value))
        except ValueError:
            return []
    if isinstance(value, dict):
        return [n for child in value.values() for n in _numeric_leaves(child)]
    if isinstance(value, (list, tuple)):
        return [n for child in value for n in _numeric_leaves(child)]
    return []


def _percent_leaves(value, path="") -> list[float]:
    if isinstance(value, dict):
        return [n for key, child in value.items() for n in _percent_leaves(child, str(key))]
    if isinstance(value, (list, tuple)):
        return [n for child in value for n in _percent_leaves(child, path)]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        if path.endswith("_pct"):
            return [float(value)]
        if 0 <= value <= 1 and any(s in path for s in ("chance", "ratio", "probability", "rate")):
            return [float(value) * 100]
    return []


def _deviation(hit: ProblemHit) -> float:
    """同名数值证据相对阈值的最大偏离；无可比较字段时由时间顺序决定。"""
    ratios = [abs(value / threshold - 1) for key, value in hit.evidence.items()
              if isinstance(value, (int, float)) and not isinstance(value, bool)
              and isinstance((threshold := hit.thresholds.get(key)), (int, float))
              and not isinstance(threshold, bool) and math.isfinite(value)
              and math.isfinite(threshold) and threshold != 0]
    return max(ratios, default=-1.0)


def problem_hits(summary: PlayerSummary, problem: str) -> list[tuple[str, ProblemHit]]:
    """返回跨场编号及命中，按场次与原始顺序排列。"""
    return [(f"{match.number}:{hit.target}", hit) for match in summary.matches
            for hit in summary.detail(match.number)[1].hits
            if hit.problem == problem and hit.target != "match"]


def representative_hits(summary: PlayerSummary, problem: str, limit: int) -> list[tuple[str, ProblemHit]]:
    """先覆盖不同场次；漏补先看是否无人补回和距离，其余按阈值偏离。"""
    def rank(hit: ProblemHit):
        if problem == "missed_trade":
            return (bool(hit.evidence.get("killer_traded_by_other")),
                    float(hit.evidence.get("distance", math.inf)),
                    int(hit.evidence.get("tick", 0)))
        return (-_deviation(hit),)

    groups: dict[str, list[tuple[str, ProblemHit]]] = {}
    for ref, hit in problem_hits(summary, problem):
        groups.setdefault(ref.partition(":")[0], []).append((ref, hit))
    for number, items in groups.items():
        moments = {m.id: (m.round, m.tick) for m in summary.detail(number)[0].moments}
        items.sort(key=lambda item: (rank(item[1]),
                                     moments.get(item[1].target,
                                                 (int(item[1].target.partition("-")[0][1:]), 0))))
    chosen = []
    while len(chosen) < limit and any(groups.values()):
        candidates = [(number, items[0]) for number, items in groups.items() if items]
        candidates.sort(key=lambda pair: (rank(pair[1][1]), pair[0]))
        used = {ref.partition(":")[0] for ref, _ in chosen}
        number, _ = next(((number, item) for number, item in candidates if number not in used),
                         candidates[0])
        chosen.append(groups[number].pop(0))
    return chosen


def summary_totals(summary: PlayerSummary) -> tuple[int, int, int, int, int, int]:
    """击杀、死亡、关键助攻和按最终比分划分的胜负平场数。"""
    matches = summary.matches
    return (sum(m.kills for m in matches), sum(m.deaths for m in matches),
            sum(m.assists for m in matches), sum(m.score[0] > m.score[1] for m in matches),
            sum(m.score[0] < m.score[1] for m in matches),
            sum(m.score[0] == m.score[1] for m in matches))


def build_summary_digest(summary: PlayerSummary, baseline: dict | None, cfg: dict) -> str:
    maps = Counter(match.map_name for match in summary.matches)
    map_text = "、".join(f"{name} {count} 场" for name, count in sorted(maps.items())) or "无"
    kills, deaths, assists, wins, losses, draws = summary_totals(summary)
    lines = ["## 多场比赛", f"玩家：{summary.primary_name}。用过的名字：{'、'.join(summary.names) or summary.steamid}。",
             f"共 {len(summary.matches)} 场，在场 {summary.rounds_played_total} 回合。地图分布：{map_text}。",
             f"合计：{kills} 杀、{deaths} 死、{assists} 次关键助攻；{wins} 胜、{losses} 负、{draws} 平。"]
    note = pro_training_note(summary.steamid, baseline, cfg)
    if note:
        lines.insert(0, note)
    lines.extend(["", "## 每场概况", *summary_match_table(summary), "",
                  "## 整场枪法（各场数值的中位数）",
                  "以下每项取各场数值的中位数，共 N 场中的参与场数见各行；不合并子弹重新计算。",
                  f"职业样本：{baseline.get('source', '无') if baseline else '无'}。仅供参考。"])
    for key, metric in summary.aim_overall.items():
        label, unit, _, _ = METRIC_LABELS[key]
        if metric.median is None:
            lines.append(f"- {label}：无可用数值，参与 0 场。")
            continue
        comparison = compare(key, metric.median, baseline)
        pro = (f"；职业中位数 {comparison['pro_median']}{unit}，"
               f"优于职业样本 {comparison['better_than_pro_pct']}% 的人次"
               if comparison else "；无可用职业对比")
        lines.append(f"- {label}：各场数值的中位数 {metric.median:.2f}{unit}，"
                     f"共 {metric.matches} 场{pro}。")
    rules = cfg["problems"]
    lines.extend(["", "## 问题频率（汇总）",
        f"突出需偶然概率低于 {rules['frequency_max_chance'] * 100:g}%，"
        f"且多出次数至少 {rules['frequency_min_excess']:g} 次。"
        "频率按在场回合计算；多场稳定高于职业更能说明是习惯，"
        "例如 8 场中 7 场高于职业，比只在一场集中出现更有说服力。",
        *summary_frequency_table(summary), "", "## 检测到的问题",
        "只能从以下命中选择问题和亮点；other 除外。编号只放在 targets 中。",
        "检测口径：slow_reaction 和 poor_crosshair_placement 只能在打中过对手的交火中判断；"
        "死亡时如果没打中对手就无法判断，因此击杀和死亡的比例不说明问题。"
        "TTD 是从看见对手到第一次命中的时间，分为“看见→开枪”和“开枪→命中”。"
        "频率按每 10 回合整体比较，不按击杀和死亡分开。"])
    limit = cfg["summary"]["representatives_per_problem"]
    for pid, spec in PROBLEMS.items():
        hits = problem_hits(summary, pid)
        if not hits:
            continue
        per_match = Counter(ref.partition(":")[0] for ref, _ in hits)
        counts = "、".join(f"{match.number} {per_match[match.number]} 次" for match in summary.matches
                          if per_match[match.number])
        lines.append(f"### {spec.label}（{pid}，共 {len(hits)} 次；{counts}）")
        lines.extend(f"- {ref}：{evidence_sentence(hit)}" for ref, hit in
                     representative_hits(summary, pid, limit))
    from cs2coach.tools.slots import slot_guidance
    lines.extend(["", slot_guidance(summary=summary)])
    lines.extend(drill_guidance(cfg))
    return "\n".join(lines) + "\n"


def summary_numbers(summary: PlayerSummary, baseline: dict | None) -> list[float]:
    """条目分析可引用的汇总、每场概况及职业比较数值。"""
    values = _numeric_leaves([len(summary.matches), summary.rounds_played_total,
                              *summary_totals(summary)])
    for match in summary.matches:
        values.extend(_numeric_leaves(match.model_dump(exclude={"aim_stats", "prominent_problems"})))
    for row in summary.frequency:
        values.extend(_numeric_leaves(row.model_dump()))
        if row.chance is not None:
            values.append(row.chance * 100)
    for key, item in summary.aim_overall.items():
        values.extend(_numeric_leaves(item.model_dump()))
        if item.median is not None:
            values.extend(_numeric_leaves(compare(key, item.median, baseline)))
    return TypedNumbers(values, summary_percentages(summary, baseline))


def summary_percentages(summary: PlayerSummary, baseline: dict | None = None) -> list[float]:
    """汇总条目可引用的百分比，保留字段类型。"""
    values = []
    for match in summary.matches:
        if match.aim_stats:
            values.extend(_percent_leaves(match.aim_stats.model_dump()))
    for row in summary.frequency:
        values.extend(_percent_leaves(row.model_dump()))
    for key, item in summary.aim_overall.items():
        if key.endswith("_pct") and item.median is not None:
            values.append(item.median)
        if item.median is not None:
            comparison = compare(key, item.median, baseline)
            if comparison:
                values.append(comparison["better_than_pro_pct"])
                if key.endswith("_pct"):
                    values.extend(comparison[name] for name in ("pro_median", "pro_p25", "pro_p75"))
    return values
