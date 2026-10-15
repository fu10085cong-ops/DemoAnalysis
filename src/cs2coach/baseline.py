"""职业基准：用与分析用户完全相同的流程，统计职业比赛中每人每场的枪法数值（docs/product.md "判定标准"）。

MVP 只有一个小样本（ESL 一个 BO3，10 名选手，30 人次），由 scripts/build_baseline.py 生成，随包分发。
报告中必须注明样本来源和大小。
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

BASELINE_FILE = Path(__file__).resolve().parent / "baselines" / "pro_sample.json"

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
