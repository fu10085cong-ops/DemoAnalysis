"""将问题前提条件、原始证据与阈值输出为可审查的 Markdown。"""

import json

from cs2coach.features.problems import PROBLEMS, ProblemReport


def render_problem_report(report: ProblemReport) -> str:
    lines = ["# 问题检测", "", "命中表示满足目录的必要条件，是否属于失误及其轻重仍需结合情境判断。", "",
             f"职业基准：{report.baseline_source or '无，仅检测不依赖基准的问题'}", ""]
    for pid, spec in PROBLEMS.items():
        hits = [h for h in report.hits if h.problem == pid]
        if not hits:
            continue
        lines.extend([f"## {spec.label}（{pid}，{len(hits)} 次）", ""])
        for h in hits:
            lines.extend([f"### {h.target} · {h.weapon or '无交火武器'}", "", "证据：", "", "```json",
                          json.dumps(h.evidence, ensure_ascii=False, indent=2, allow_nan=False), "```", "",
                          "阈值：", "", "```json",
                          json.dumps(h.thresholds, ensure_ascii=False, indent=2, allow_nan=False), "```", ""])
    if not report.hits:
        lines.extend(["未命中已启用的检测规则。", ""])
    lines.extend(["## 停用与缺少数据", "", "停用：" + ("、".join(report.disabled) or "无"), "",
                  "skipped 按问题统计缺少必要数据或职业阈值而无法判断的次数；同类问题可包含交火和整场段。", ""])
    lines.extend([f"- {pid}：{n}" for pid, n in report.skipped.items()] or ["无跳过记录。"])
    return "\n".join(lines) + "\n"
