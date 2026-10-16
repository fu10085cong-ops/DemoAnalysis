"""多场 AI 报告：程序事实、跨场依据与跳转命令。"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from cs2coach.features.problems import PROBLEMS
from cs2coach.config import load_config
from cs2coach.report.evidence import evidence_sentence
from cs2coach.report.facts import missed_trade_facts, render_moment_card
from cs2coach.report.render import SEVERITY
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.summary.render import summary_frequency_table, summary_match_table
from cs2coach.tools.drills import drill_labels

if TYPE_CHECKING:
    from cs2coach.summary.diagnose import SummaryDiagnosis


def render_summary_diagnosis(d: SummaryDiagnosis, summary: PlayerSummary,
                             baseline: dict | None, cfg: dict | None = None) -> str:
    sub = d.submission
    labels = drill_labels(cfg or load_config())
    matches = {match.number: match for match in summary.matches}
    maps = Counter(match.map_name for match in summary.matches)
    map_text = "、".join(f"{name} {count} 场" for name, count in sorted(maps.items()))
    lines = [f"# 多场复盘：{summary.primary_name} · {len(summary.matches)} 场",
             "", f"曾用名：{'、'.join(summary.names) or '未知'} · 地图：{map_text or '无'} · "
             f"模型 {d.provider} / {d.model}", "", "## 总览", "", sub.overview,
             "", "## 突出问题", ""]
    prominent = [row for row in summary.frequency if row.prominent]
    lines.extend(summary_frequency_table(summary, prominent) if prominent else
                 ["没有同时达到偶然概率和多出次数条件的突出问题。"])

    def label(pid):
        return "其他（AI 判断，无程序依据）" if pid == "other" else PROBLEMS[pid].label

    def target_parts(target):
        number, _, local = target.partition(":")
        return matches[number], summary.detail(number), local

    def facts(entry):
        lines.extend(["", "**发生了什么**", ""])
        for target in entry.targets:
            match, (mm, report), local = target_parts(target)
            lines.extend([f"#### 〔{target}〕{match.number} · {match.match_id} · {match.map_name}", ""])
            moment = next((m for m in mm.moments if m.id == local), None)
            if moment is not None:
                names = {m.opponent: m.opponent_name for m in mm.moments
                         if m.opponent is not None and m.opponent_name}
                card = render_moment_card(moment, names).split("\n\n", 1)[1]
                lines.extend([card, ""])
            else:
                rows = [row for row in mm.missed_trades if local == f"R{row.round}"]
                lines.extend(f"- {text}" for row in rows for text in missed_trade_facts(row, {}))
                lines.append("")
        lines.extend(["**程序检测依据**", ""])
        if entry.problem == "other":
            lines.append("AI 判断，无程序依据。")
        else:
            for target in entry.targets:
                _, (_, report), local = target_parts(target)
                lines.extend(f"- 〔{target}〕{evidence_sentence(hit)}" for hit in report.hits
                             if hit.problem == entry.problem and hit.target == local)

    lines.extend(["", "## 关键问题", ""])
    for entry in sub.findings:
        lines.extend([f"### {label(entry.problem)} · {SEVERITY[entry.severity]}"])
        facts(entry)
        lines.extend(["", "**分析**", "", entry.analysis, "", "**怎么练**", ""])
        lines.extend(f"- {labels[drill]}" for drill in entry.drills if drill in labels)
        lines.extend(["", entry.training, ""])
    lines.extend(["## 反复出现的问题", ""])
    for entry in sub.patterns:
        lines.extend([f"### {label(entry.problem)}", ""])
        groups: dict[str, list[str]] = {}
        for target in entry.targets:
            number = target.partition(":")[0]
            groups.setdefault(number, []).append(target)
        for number, targets in groups.items():
            match = matches[number]
            lines.append(f"- {number} · {match.match_id} · {match.map_name}："
                         + "、".join(f"〔{target}〕" for target in targets))
        lines.extend(["", entry.analysis, ""])
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
    lines.extend(f"{i}. {item}" for i, item in enumerate(sub.training_plan, 1))
    lines.extend(["", "## 附录：每场概况", "", *summary_match_table(summary),
                  "", "## 附录：跳转命令", "",
                  "先在游戏里打开对应的 demo，再执行该行命令。", "",
                  "| 比赛 | 编号 | tick | 命令 |", "|---|---|---:|---|"])
    targets = dict.fromkeys(target for entry in [*sub.findings, *sub.patterns, *sub.highlights]
                            for target in entry.targets)
    for target in targets:
        match, (mm, _), local = target_parts(target)
        moment = next((m for m in mm.moments if m.id == local), None)
        if moment is not None:
            lines.append(f"| {match.number} · {match.match_id} | {target} | {moment.tick} | "
                         f"`{moment.jump_command}` |")
    lines.extend(["", "---", "",
                  f"职业样本来源：{baseline.get('source') if baseline else '无可用职业基准'}。样本仅作参考。"])
    deleted = sum(item.action == "removed" for item in d.dropped)
    if deleted:
        lines.extend(["", f"有 {deleted} 条结论未通过数据检查，已删除。"])
    return "\n".join(lines).rstrip() + "\n"
