"""分级报告实验（2026-10-16）：L0 纯程序 + L1 弱模型逐个交火改写两三句话。

L0：突出问题（没有时取多出次数为正的前几项）、程序排出的代表交火、事实卡、程序证据、
固定说明（config/problem_text.toml）和练习图，全部由程序生成。
L1：每个代表交火单独调用一次模型，只给这一次交火的事实和可用占位符，要求写两三句话；
用现有检查器核对，不通过就退回修改，仍不通过则不写这段（报告里只保留程序事实）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping

from cs2coach.features.models import MatchMoments
from cs2coach.features.problems import PROBLEMS, FrequencyRow, ProblemReport
from cs2coach.features.ranking import candidate_targets
from cs2coach.report.evidence import evidence_sentence
from cs2coach.report.facts import missed_trade_facts, render_moment_card
from cs2coach.report.problems import frequency_table
from cs2coach.tools.checker import Violation, check_submission
from cs2coach.tools.drills import drill_labels
from cs2coach.tools.slots import MOMENT_GUIDE, PROBLEM_VALUES, ROUND_FIELDS, SlotError, make_slot_resolver, render_slots
from cs2coach.tools.submission import parse_submission

TEXT_FILE = Path(__file__).resolve().parents[3] / "config" / "problem_text.toml"

L1_SYSTEM = """你是 CS2 复盘助手，只负责一件小事：根据程序给出的一次交火的事实，用两到三句中文说明这次交火发生了什么、为什么属于指定的问题。

规则：
- 只根据给出的事实写，不要补充数据里没有的内容：地点类型（转角、门口）、准星高度或瞄准部位、移动方向（后退、前压）、声音和报点、对手意图都没有。
- 文字里不写任何数字，也不写“两次”“三发”这类中文数字；需要数字时用下面列出的占位符，原样照抄花括号里的内容，后面不要再写单位。
- 讲局势时用 situation 占位符，不要自己判断守包、回防或人数优劣。
- 不写改进建议，建议由程序给出。不写编号，不用“都”“始终”。
- 直接输出这两三句话，不要标题、列表或解释。"""


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


def _slot_menu(target: str, problem: str, resolver) -> str:
    """这次交火可用的占位符及其当前显示值，弱模型照抄即可。"""
    lines = []
    if target.startswith("R") and "-" not in target:  # 漏补回合
        values = [resolver(f"{target}.{f}") for f in ROUND_FIELDS]
        several = any(isinstance(v, SlotError) and "[1]" in v.message for v in values)
        objects = ([f"{target}[{i}]" for i in range(1, int(resolver(f"{target}.count")) + 1)]
                   if several else [target])
        for obj in objects:
            for f in ("situation", "people", "round_time", "distance"):
                value = resolver(f"{obj}.{f}")
                if not isinstance(value, SlotError):
                    lines.append(f"- {{{obj}.{f}}} → {value}")
        lines.append(f"- {{{target}.count}} → {resolver(f'{target}.count')}（本回合漏补次数）")
        return "\n".join(lines)
    fields = [f.strip() for name, _, _ in MOMENT_GUIDE for f in name.split("/")]
    meaning = {f.strip(): m for name, m, _ in MOMENT_GUIDE for f in name.split("/")}
    for f in fields:
        if f in ("value", "threshold", "gap") and problem not in PROBLEM_VALUES:
            continue
        value = resolver(f"{target}.{f}")
        if isinstance(value, SlotError):
            continue
        lines.append(f"- {{{target}.{f}}} → {value}（{meaning[f].split('。')[0].replace('**', '')}）")
    return "\n".join(lines)


def write_l1(section: Section, example: Example, mm: MatchMoments, problems: ProblemReport,
             frequency: list[FrequencyRow], baseline: dict | None, score, cfg: dict,
             provider_factory: Callable[[], Any], max_repairs: int = 2) -> Example:
    """单个交火的两三句话；检查器只看这段文字（借用一条 finding 的全部规则）。"""
    started = perf_counter()
    resolver = make_slot_resolver(mm, problems, score, frequency, baseline,
                                  problem=section.problem, targets=(example.target,))
    user = (f"问题：{section.label}\n程序判定依据：{example.evidence}\n\n这次交火的事实：\n{example.card}\n\n"
            f"可用的占位符（左边原样照抄，右边是它会显示成的样子）：\n{_slot_menu(example.target, section.problem, resolver)}")
    provider = provider_factory()
    provider.start(L1_SYSTEM, [user], [])
    for attempt in range(max_repairs + 1):
        example.attempts = attempt + 1
        turn = provider.send()
        raw = (turn.text or "").strip()
        example.raw = raw
        sub = parse_submission(dict(overview="本场值得复盘。", findings=[dict(
            problem=section.problem, targets=[example.target], severity="medium", analysis=raw or "空。",
            training="练。", drills=[])], patterns=[], highlights=[], training_plan=["练。"]))
        found = [v for v in check_submission(sub, mm, problems, "", cfg, None, score, frequency, baseline)
                 if v.path.startswith("findings[0].analysis")]
        if not raw:
            found.append(Violation(path="findings[0].analysis", code="empty", message="没有输出文字。"))
        example.violations.append([f"{v.code}：{v.message[:120]}" for v in found])
        if not found:
            example.text = render_slots(raw, resolver)[0]
            break
        provider.add_user("这段文字没有通过检查，请按下面的问题修改后，只输出修改后的两三句话：\n"
                          + "\n".join(f"- {v.message}" for v in found))
    example.seconds = perf_counter() - started
    return example


def render_tiered(mm: MatchMoments, sections: list[Section], frequency: list[FrequencyRow], score,
                  model: str | None) -> str:
    lines = [f"# 分级复盘（实验）：{mm.player_name} · {mm.map_name}", "",
             f"比分 {score[0]}:{score[1]} · L0 程序 + L1 {model or '无'}", "", "## 问题频率", ""]
    lines += frequency_table([r for r in frequency if r in choose_problems(frequency)])
    for s in sections:
        lines += ["", f"## {s.label}", "", f"**为什么是问题**：{s.why}", "", f"**怎么改**：{s.fix}"]
        if s.drills:
            lines += ["", "**练习**：" + "、".join(s.drills)]
        for e in s.examples:
            lines += ["", e.card, "", f"程序依据：{e.evidence}"]
            if e.text:
                lines += ["", f"**说明**：{e.text}"]
    return "\n".join(lines).rstrip() + "\n"
