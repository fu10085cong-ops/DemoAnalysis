"""将问题前提条件、原始证据与阈值输出为可审查的 Markdown。"""

import json

from cs2coach.features.problems import PROBLEMS, FrequencyRow, ProblemReport


def frequency_cells(row: FrequencyRow) -> list[str]:
    """单场和多场共用的六列显示口径。"""
    name = f"**{row.label}**" if row.prominent else row.label
    if row.needs_more_matches:
        name += "（次数较少，需要更多比赛确认）"
    elif row.excess_hits is not None and row.excess_hits <= 0:
        name += "（不高于职业水平）"
    return [name, str(row.hits),
            f"{row.expected_hits:.2f}" if row.expected_hits is not None else "—",
            f"{row.excess_hits:.2f}" if row.excess_hits is not None else "—",
            f"{row.chance * 100:.1f}%" if row.chance is not None else "—",
            f"{row.ratio:.2f}×" if row.ratio is not None else "—"]


def frequency_table(rows: list[FrequencyRow]) -> list[str]:
    """摘要、程序检测和 AI 报告共用同一组频率列。"""
    lines = ["| 问题 | 本场次数 | 按职业频率预期 | 多出的次数 | 偶然概率 | 倍数 |",
             "|---|---:|---:|---:|---:|---:|"]
    lines.extend("| " + " | ".join(frequency_cells(row)) + " |" for row in rows)
    return lines


def render_problem_report(report: ProblemReport, frequencies: list[FrequencyRow] | None = None) -> str:
    lines = ["# 问题检测", "", "命中表示满足目录的必要条件，是否属于失误及其轻重仍需结合情境判断。", "",
             f"职业基准：{report.baseline_source or '无，仅检测不依赖基准的问题'}", ""]
    if frequencies is not None:
        lines.extend(["## 频率对比", "", "加粗项的偶然概率和多出次数均达到配置条件；命中不等于失误。", ""])
        lines.extend(frequency_table(frequencies))
        lines.append("")
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
                  "skipped 中问题编号表示缺少数据或阈值；问题/条件表示命中被该条件排除的次数。", ""])
    lines.extend([f"- {pid}：{n}" for pid, n in report.skipped.items()] or ["无跳过记录。"])
    return "\n".join(lines) + "\n"
