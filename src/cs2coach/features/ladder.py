"""分段人均标准上的位置与达到档位。"""

from __future__ import annotations

import math

from pydantic import BaseModel


class LadderPoint(BaseModel):
    level: int
    tier_id: str
    name: str
    standard: float
    q25: float | None = None
    q75: float | None = None


class LadderResult(BaseModel):
    position: float | None
    reached: LadderPoint | None
    next: LadderPoint | None
    ordered: bool
    text: str = ""


def ladder_points(metric: str, tiers: dict[str, dict], reach_quantile: float = 0.25) -> list[LadderPoint]:
    """只收有档位和人均数据的分段；其他字段不包含身份。"""
    points = []
    section, _, metric_id = metric.partition(":")
    if not metric_id:
        section, metric_id = "metrics", metric
    elif section == "panel":
        section = "panel_metrics"
    for tier_id, tier in tiers.items():
        stats = tier.get("per_player", {}).get(section, {}).get(metric_id, {})
        standard = stats.get("standard")
        if tier.get("level") is None or standard is None or not math.isfinite(standard):
            continue
        values = stats.get("values", [])
        q25, q75 = stats.get("q25"), stats.get("q75")
        if reach_quantile != 0.25 and len(values) >= 2:
            # 与 baseline.build_baseline 相同的 inclusive 线性分位。
            def at(p):
                offset = (len(values) - 1) * p
                lo = math.floor(offset)
                return values[lo] + (values[math.ceil(offset)] - values[lo]) * (offset - lo)
            q25, q75 = at(reach_quantile), at(1 - reach_quantile)
        points.append(LadderPoint(level=tier["level"], tier_id=tier_id, name=tier["name"],
                                  standard=standard, q25=q25, q75=q75))
    return sorted(points, key=lambda point: point.level)


def ladder_position(value: float | None, points: list[LadderPoint], higher_is_better: bool) -> LadderResult:
    """按标准插值，按较差侧分位判断已达到的最高档。"""
    points = sorted(points, key=lambda point: point.level)
    if len(points) < 2:
        return LadderResult(position=None, reached=None, next=points[0] if points else None, ordered=True)
    sign = 1 if higher_is_better else -1
    ordered = all(sign * (right.standard - left.standard) > 0 and right.level > left.level
                  for left, right in zip(points, points[1:]))
    if not ordered or value is None or not math.isfinite(value):
        return LadderResult(position=None, reached=None, next=None, ordered=ordered)
    reached = next((point for point in reversed(points) if sign * value >= sign *
                    ((point.q25 if higher_is_better else point.q75) if
                     (point.q25 if higher_is_better else point.q75) is not None else point.standard)), None)
    following = next((point for point in points if reached is None or point.level > reached.level), None)
    if sign * value < sign * points[0].standard:
        left, right = points[:2]
    elif sign * value >= sign * points[-1].standard:
        left, right = points[-2:]
    else:
        left, right = next((a, b) for a, b in zip(points, points[1:])
                           if sign * a.standard <= sign * value <= sign * b.standard)
    position = left.level + (value - left.standard) * (right.level - left.level) / (right.standard - left.standard)
    position = max(points[0].level - 0.5, min(points[-1].level + 0.5, position))
    return LadderResult(position=position, reached=reached, next=following, ordered=True)
