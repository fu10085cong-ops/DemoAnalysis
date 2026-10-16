"""基础版复盘报告（L0，免费档）：完全由程序生成，不调用 AI。

内容：比分与整场枪法对比、突出问题、每类问题的固定说明（config/problem_text.toml）、
程序挑出的代表交火事实卡与检测依据、练习图、跳转命令。产品负责人 2026-10-16 确定：免费档只提供这一版。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from cs2coach.baseline import compare
from cs2coach.features.models import MatchMoments
from cs2coach.features.problems import PROBLEMS, FrequencyRow, ProblemReport
from cs2coach.features.ranking import candidate_targets
from cs2coach.report.evidence import evidence_sentence
from cs2coach.report.facts import missed_trade_facts, render_moment_card
from cs2coach.report.problems import frequency_table
from cs2coach.tools.drills import drill_labels

TEXT_FILE = Path(__file__).resolve().parents[3] / "config" / "problem_text.toml"


@dataclass
class Example:
    target: str
    card: str
    evidence: str
    text: str | None = None        # L1 渲染后的文字；None 表示没写成
    raw: str | None = None         # L1 原始输出（含占位符）
    attempts: int = 0
    violations: list[list[str]] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class Section:
    problem: str
    label: str
    why: str
    fix: str
    drills: list[str]
    examples: list[Example]


def problem_texts(path: Path = TEXT_FILE) -> dict[str, dict[str, str]]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def choose_problems(frequency: list[FrequencyRow], limit: int = 3) -> list[FrequencyRow]:
    """突出问题优先；没有突出问题时取多出次数为正的前几项（标注需要更多比赛确认）。"""
    prominent = [r for r in frequency if r.prominent]
    if prominent:
        return prominent[:limit]
    rest = sorted((r for r in frequency if (r.excess_hits or 0) > 0), key=lambda r: -(r.excess_hits or 0))
    return rest[:limit]


def build_l0(mm: MatchMoments, problems: ProblemReport, frequency: list[FrequencyRow], cfg: dict,
             names: Mapping[int, str], per_problem: int = 2) -> list[Section]:
    texts = problem_texts()
    labels = drill_labels(cfg)
    moments = {m.id: m for m in mm.moments}
    sections = []
    for row in choose_problems(frequency):
        pid = row.problem
        text = texts.get(pid, {"why": "", "fix": ""})
        drills = [labels[d["id"]] for d in cfg["training"]["drills"]
                  if pid in d["problems"] and "min_aim_problems" not in d]
        examples = []
        for target in candidate_targets(mm, problems.hits, pid, per_problem):
            hit = next(h for h in problems.hits if h.problem == pid and h.target == target)
            if target in moments:
                card = render_moment_card(moments[target], names).replace("### ", "#### ", 1)
            else:  # 漏补回合：用漏补记录的事实
                rows = [r for r in mm.missed_trades if f"R{r.round}" == target]
                card = "\n".join([f"#### 〔{target}〕", ""] + [f"- {x}" for r in rows for x in missed_trade_facts(r, names)])
            examples.append(Example(target=target, card=card,
                                    evidence=evidence_sentence(hit)))
        sections.append(Section(pid, PROBLEMS[pid].label, text["why"], text["fix"], drills, examples))
    return sections


def _aim_table(mm: MatchMoments, baseline: dict[str, Any] | None) -> list[str]:
    from cs2coach.tools.digest import METRIC_LABELS

    lines = ["| 指标 | 本场 | 职业中位数 | 优于职业样本 | 样本数 |", "|---|---:|---:|---:|---:|"]
    stats = mm.aim_stats
    for metric, (label, unit, count_field, count_unit) in METRIC_LABELS.items():
        value = getattr(stats, metric, None) if stats else None
        if value is None:
            continue
        c = compare(metric, value, baseline)
        pro = f"{c['pro_median']:g}{unit}" if c else "—"
        better = f"{c['better_than_pro_pct']}%" if c else "—"
        lines.append(f"| {label} | {value:g}{unit} | {pro} | {better} | {getattr(stats, count_field)} {count_unit} |")
    return lines


def render_basic_report(mm: MatchMoments, sections: list[Section], frequency: list[FrequencyRow], score,
                        baseline: dict[str, Any] | None, cfg: dict,
                        problems: ProblemReport) -> str:
    """基础版报告正文；数字全部来自程序，不含 AI 文字。"""
    kills = sum(m.kind == "kill" for m in mm.moments)
    deaths = sum(m.kind == "death" for m in mm.moments)
    assists = sum(m.kind == "assist" for m in mm.moments)
    lines = [f"# 基础复盘：{mm.player_name} · {mm.map_name}", "",
             f"比分 {score[0]}:{score[1]} · 击杀 {kills}、死亡 {deaths}、关键助攻 {assists} · 程序生成，不含 AI 分析", "",
             ]
    if mm.lobby:
        from cs2coach.report.card import render_front
        lines.extend(render_front(mm, problems, frequency, cfg, baseline))
    lines.extend(["## 整场枪法", "", *_aim_table(mm, baseline), "", "## 突出问题", ""])
    chosen = {r.problem for r in choose_problems(frequency)}
    if not chosen:
        lines.append("本场没有比职业频率偏多的问题。")
    else:
        if not any(r.prominent for r in frequency):
            lines += ["本场没有达到突出标准的问题；下面是比职业频率偏多的几项，次数较少，需要更多比赛确认。", ""]
        lines += frequency_table([r for r in frequency if r.problem in chosen])
    jumps = []
    for s in sections:
        lines += ["", f"## {s.label}", "", f"**为什么是问题**：{s.why}", "", f"**怎么改**：{s.fix}", "",
                  "**练习**：" + ("、".join(s.drills) if s.drills else "暂不指定练习图，按上面的改法在实战中练习。")]
        if s.examples:
            lines += ["", "**本场的代表交火**"]
        for e in s.examples:
            lines += ["", e.card, "", f"程序依据：{e.evidence}"]
            jumps += [m for m in mm.moments if m.id == e.target and m not in jumps]
    if jumps:
        lines += ["", "## 跳转命令", "", "先在游戏里打开这场 demo，再执行对应的命令。", "",
                  "| 编号 | 回合 | 命令 |", "|---|---:|---|"]
        lines += [f"| {m.id} | {m.round} | `{m.jump_command}` |" for m in jumps]
    lines += ["", "---", "", "想要逐条分析原因和个性化训练计划，可以生成 AI 版报告（加 --ai）。"]
    return "\n".join(lines).rstrip() + "\n"


