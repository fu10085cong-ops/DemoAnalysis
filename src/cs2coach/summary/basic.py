"""多场基础版复盘报告（L0）：只用程序事实和固定文案。"""

from __future__ import annotations

from collections import Counter

from cs2coach.baseline import compare
from cs2coach.features.problems import PROBLEMS
from cs2coach.report.basic import Example, Section, choose_problems, problem_texts
from cs2coach.report.evidence import evidence_sentence
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.summary.digest import representative_hits, summary_totals
from cs2coach.summary.render import summary_frequency_table, summary_match_table
from cs2coach.summary.render_report import summary_target_card, summary_target_parts
from cs2coach.tools.digest import METRIC_LABELS
from cs2coach.tools.drills import drill_labels


def build_summary_l0(summary: PlayerSummary, cfg: dict) -> list[Section]:
    texts = problem_texts()
    labels = drill_labels(cfg)
    sections = []
    for row in choose_problems(summary.frequency):
        pid = row.problem
        wording = texts.get(pid, {"why": "", "fix": ""})
        drills = [labels[d["id"]] for d in cfg["training"]["drills"]
                  if pid in d["problems"] and "min_aim_problems" not in d]
        examples = [Example(target=target, card="\n".join(summary_target_card(summary, target)).rstrip(),
                            evidence=evidence_sentence(hit))
                    for target, hit in representative_hits(
                        summary, pid, cfg["summary"]["representatives_per_problem"])]
        sections.append(Section(pid, PROBLEMS[pid].label, wording["why"], wording["fix"], drills, examples))
    return sections


def render_summary_basic_report(summary: PlayerSummary, sections: list[Section],
                                baseline: dict | None) -> str:
    maps = Counter(match.map_name for match in summary.matches)
    map_text = "、".join(f"{name} {count} 场" for name, count in sorted(maps.items())) or "无"
    kills, deaths, assists, _, _, _ = summary_totals(summary)
    lines = [f"# 基础复盘（多场）：{summary.primary_name} · {len(summary.matches)} 场", "",
             f"曾用名：{'、'.join(summary.names) or '未知'} · 地图：{map_text} · "
             f"在场 {summary.rounds_played_total} 回合 · 击杀 {kills}、死亡 {deaths}、关键助攻 {assists}"
             " · 程序生成，不含 AI 分析", "", "## 整场枪法", "",
             "| 指标 | 各场中位数 | 职业中位数 | 优于职业样本 | 参与场数 |",
             "|---|---:|---:|---:|---:|"]
    for metric, (label, unit, _, _) in METRIC_LABELS.items():
        item = summary.aim_overall.get(metric)
        if item is None or item.median is None:
            continue
        comp = compare(metric, item.median, baseline)
        pro = f"{comp['pro_median']:g}{unit}" if comp else "—"
        better = f"{comp['better_than_pro_pct']}%" if comp else "—"
        lines.append(f"| {label} | {item.median:g}{unit} | {pro} | {better} | {item.matches} 场 |")
    lines.extend(["", "## 突出问题", ""])
    chosen = {section.problem for section in sections}
    if not chosen:
        lines.append("这批比赛没有比职业频率偏多的问题。")
    else:
        if not any(row.prominent for row in summary.frequency):
            lines.extend(["这批比赛没有达到突出标准的问题；下面是比职业频率偏多的几项，次数较少，需要更多比赛确认。", ""])
        lines.extend(summary_frequency_table(summary, [row for row in summary.frequency if row.problem in chosen]))
    jumps = {}
    for section in sections:
        lines.extend(["", f"## {section.label}", "", f"**为什么是问题**：{section.why}", "",
                      f"**怎么改**：{section.fix}", "",
                      "**练习**：" + ("、".join(section.drills) if section.drills else
                                    "暂不指定练习图，按上面的改法在实战中练习。")])
        if section.examples:
            lines.extend(["", "**代表交火**"])
        for example in section.examples:
            lines.extend(["", example.card, "", f"程序依据：{example.evidence}"])
            match, (mm, _), local = summary_target_parts(summary, example.target)
            moment = next((m for m in mm.moments if m.id == local), None)
            if moment is not None:
                jumps[example.target] = (match, moment)
    lines.extend(["", "## 附录：每场概况", "", *summary_match_table(summary)])
    if jumps:
        lines.extend(["", "## 附录：跳转命令", "", "先在游戏里打开对应的 demo，再执行该行命令。", "",
                      "| 比赛 | 编号 | tick | 命令 |", "|---|---|---:|---|"])
        lines.extend(f"| {match.number} · {match.match_id} | {target} | {moment.tick} | "
                     f"`{moment.jump_command}` |" for target, (match, moment) in jumps.items())
    lines.extend(["", "---", "", "想要逐条分析原因和个性化训练计划，可以生成 AI 版报告（加 --ai）。"])
    return "\n".join(lines).rstrip() + "\n"
