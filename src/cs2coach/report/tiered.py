"""分级报告实验（2026-10-16）：L1 弱模型逐个交火写两三句话；L0 在 report/basic.py。

L0：突出问题（没有时取多出次数为正的前几项）、程序排出的代表交火、事实卡、程序证据、
固定说明（config/problem_text.toml）和练习图，全部由程序生成。
L1：每个代表交火单独调用一次模型，只给这一次交火的事实和可用占位符，要求写两三句话；
用现有检查器核对，不通过就退回修改，仍不通过则不写这段（报告里只保留程序事实）。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from cs2coach.features.models import MatchMoments
from cs2coach.features.problems import FrequencyRow, ProblemReport
from cs2coach.report.basic import Example, Section, build_l0, choose_problems  # noqa: F401  L0 在 basic.py
from cs2coach.report.problems import frequency_table
from cs2coach.tools.checker import Violation, check_submission
from cs2coach.tools.slots import MOMENT_GUIDE, PROBLEM_VALUES, ROUND_FIELDS, SlotError, make_slot_resolver, render_slots
from cs2coach.tools.submission import parse_submission

L1_SYSTEM = """你是 CS2 复盘助手，只负责一件小事：根据程序给出的一次交火的事实，用两到三句中文说明这次交火发生了什么、为什么属于指定的问题。

规则：
- 只根据给出的事实写，不要补充数据里没有的内容：地点类型（转角、门口）、准星高度或瞄准部位、移动方向（后退、前压）、声音和报点、对手意图都没有。
- 文字里不写任何数字，也不写“两次”“三发”这类中文数字；需要数字时用下面列出的占位符，原样照抄花括号里的内容，后面不要再写单位。
- 讲局势时用 situation 占位符，不要自己判断守包、回防或人数优劣。
- 不写改进建议，建议由程序给出。不写编号，不用“都”“始终”。
- 直接输出这两三句话，不要标题、列表或解释。"""


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
