"""问题命中的代表性排序：单场候选与多场代表共用同一规则（任务 020）。"""

from __future__ import annotations

import math

from cs2coach.features.models import MatchMoments
from cs2coach.features.problems import ProblemHit


def deviation(hit: ProblemHit) -> float:
    """同名数值证据相对阈值的最大偏离；无可比较字段时为 -1，由时间顺序决定。"""
    ratios = [abs(value / threshold - 1) for key, value in hit.evidence.items()
              if isinstance(value, (int, float)) and not isinstance(value, bool)
              and isinstance((threshold := hit.thresholds.get(key)), (int, float))
              and not isinstance(threshold, bool) and math.isfinite(value)
              and math.isfinite(threshold) and threshold != 0]
    return max(ratios, default=-1.0)


def rank_key(problem: str, hit: ProblemHit) -> tuple:
    """越小越有代表性。漏补：先看是否无人补回，再看距离；其余：阈值偏离从大到小。"""
    if problem == "missed_trade":
        return (bool(hit.evidence.get("killer_traded_by_other")),
                float(hit.evidence.get("distance", math.inf)),
                int(hit.evidence.get("tick", 0)))
    return (-deviation(hit),)


def time_key(mm: MatchMoments, hit: ProblemHit) -> tuple[int, int]:
    """并列时按时间先后：瞬间取 (回合, tick)，回合目标取 (回合, 0)。"""
    for m in mm.moments:
        if m.id == hit.target:
            return (m.round, m.tick)
    try:
        return (int(hit.target.partition("-")[0][1:]), 0)
    except ValueError:
        return (0, 0)


def ranked_hits(mm: MatchMoments, hits: list[ProblemHit], problem: str) -> list[ProblemHit]:
    """单场同一问题的命中按代表性排序；不含整场目标 match。"""
    items = [h for h in hits if h.problem == problem and h.target != "match"]
    return sorted(items, key=lambda h: (rank_key(problem, h), time_key(mm, h)))


def candidate_targets(mm: MatchMoments, hits: list[ProblemHit], problem: str, limit: int) -> list[str]:
    """finding 可选的前 limit 个 target（去重保序）；命中不超过 limit 时全部可选。"""
    return list(dict.fromkeys(h.target for h in ranked_hits(mm, hits, problem)))[:limit]
