"""结构化诊断的排版：事实与检测依据由程序生成，AI 文字单独呈现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from cs2coach.features.labels import absent_rounds_note
from cs2coach.features.models import MatchMoments
from cs2coach.features.problems import PROBLEMS
from cs2coach.report.evidence import evidence_sentence, match_fact
from cs2coach.report.facts import missed_trade_facts, render_moment_card
from cs2coach.report.problems import frequency_table

if TYPE_CHECKING:
    from cs2coach.ai.diagnose import Diagnosis

SEVERITY = {"high": "高", "medium": "中", "low": "低"}


def render_diagnosis(d: Diagnosis, mm: MatchMoments, names: Mapping[int, str]) -> str:
    sub = d.submission
    by_id = {m.id: m for m in mm.moments}
    lines = [f"# 复盘报告：{mm.player_name} · {mm.map_name}", "",
             f"比分 {d.score[0]}:{d.score[1]} · 模型 {d.provider} / {d.model}", "",
             *([absent_rounds_note(mm.absent_rounds), ""] if mm.absent_rounds else []),
             "## 总览", "", sub.overview, "", "## 突出问题", ""]
    prominent = [row for row in d.frequency if row.prominent]
    if prominent:
        lines.extend(frequency_table(prominent))
    else:
        lines.append("没有同时达到偶然概率和多出次数条件的突出问题。")

    def label(pid):
        return "其他（AI 判断，无程序依据）" if pid == "other" else PROBLEMS[pid].label

    def facts(entry):
        lines.extend(["", "**发生了什么**", ""])
        for target in entry.targets:
            if target in by_id:
                lines.extend([render_moment_card(by_id[target], names).replace("### ", "#### ", 1), ""])
            elif target == "match":
                lines.extend(f"- {match_fact(h)}" for h in d.problems.hits if h.problem == entry.problem and h.target == "match")
            else:
                rows = [r for r in mm.missed_trades if target == f"R{r.round}"]
                lines.append(f"#### 〔{target}〕")
                lines.append("")
                for r in rows:
                    lines.extend(f"- {text}" for text in missed_trade_facts(r, names))
                if not rows:
                    lines.append("该回合没有漏补枪记录，未提供回合级事实卡。")
                lines.append("")
        lines.extend(["", "**程序检测依据**", ""])
        if entry.problem == "other":
            lines.append("AI 判断，无程序依据。")
        else:
            lines.extend(f"- 〔{h.target}〕{evidence_sentence(h)}" for h in d.problems.hits
                         if h.problem == entry.problem and h.target in entry.targets)

    lines.extend(["", "## 关键问题", ""])
    for entry in sub.findings:
        lines.append(f"### {label(entry.problem)} · {SEVERITY[entry.severity]}")
        facts(entry)
        lines.extend(["", "**分析**", "", entry.analysis, "", "**怎么练**", "", entry.training, ""])
    lines.extend(["## 反复出现的问题", ""])
    for entry in sub.patterns:
        lines.extend([f"### {label(entry.problem)}", "", "涉及：" + "、".join(f"〔{t}〕" for t in entry.targets),
                      "", entry.analysis, ""])
    if not sub.patterns:
        lines.extend(["没有提交反复出现的问题。", ""])
    lines.extend(["## 亮点", ""])
    for entry in sub.highlights:
        lines.append(f"### {label(entry.problem)}")
        facts(entry)
        lines.extend(["", "**分析**", "", entry.analysis, ""])
    if not sub.highlights:
        lines.extend(["没有提交亮点。", ""])
    lines.extend(["## 训练计划", ""])
    lines.extend(f"{i}. {text}" for i, text in enumerate(sub.training_plan, 1))
    lines.extend(["", "## 跳转命令", ""])
    targets = dict.fromkeys(t for entry in [*sub.findings, *sub.patterns, *sub.highlights] for t in entry.targets)
    jumps = [by_id[t] for t in targets if t in by_id]
    if jumps:
        lines.extend(["| 编号 | 回合 | tick | 命令 |", "|---|---|---|---|"])
        lines.extend(f"| {m.id} | {m.round} | {m.tick} | `{m.jump_command}` |" for m in jumps)
    else:
        lines.append("本报告未引用可跳转的瞬间。")
    lines.extend(["", "---", "", f"职业样本来源：{d.problems.baseline_source or '无可用职业基准'}。样本仅作参考。"])
    deleted = sum(item.action == "removed" for item in d.dropped)
    if deleted:
        lines.extend(["", f"有 {deleted} 条结论未通过数据检查，已删除。"])
    return "\n".join(lines).rstrip() + "\n"
