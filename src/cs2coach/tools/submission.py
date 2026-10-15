"""结构化报告的数据格式；与具体 AI 服务及调用流程无关。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from cs2coach.features.problems import PROBLEMS, ProblemReport

TARGET_PATTERN = r"^(R\d+(-(kill\d+|death|assist\d+))?|match)$"
SUMMARY_TARGET_PATTERN = r"^M\d+:R\d+(-(kill\d+|death|assist\d+))?$"


class _SubmissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Finding(_SubmissionModel):
    problem: str
    targets: list[str]
    severity: Literal["high", "medium", "low"]
    analysis: str
    training: str


class Pattern(_SubmissionModel):
    problem: str
    targets: list[str]
    analysis: str


class Highlight(_SubmissionModel):
    problem: str
    targets: list[str]
    analysis: str


class ReportSubmission(_SubmissionModel):
    overview: str
    findings: list[Finding]
    patterns: list[Pattern]
    highlights: list[Highlight]
    training_plan: list[str]


class SubmissionError(ValueError):
    """提交缺字段、类型不符或带额外字段，不能进入内容检查。"""


def parse_submission(args: dict) -> ReportSubmission:
    """只校验结构；动态 enum、数量和长度由 submit_tool 的 schema 约束。"""
    try:
        return ReportSubmission.model_validate(args)
    except ValidationError as exc:
        reasons = {"missing": "缺少必填字段", "extra_forbidden": "不允许额外字段",
                   "string_type": "应为字符串", "list_type": "应为数组",
                   "model_type": "应为对象", "literal_error": "取值不在允许范围内"}
        errors = []
        for error in exc.errors(include_url=False, include_input=False):
            path = ""
            for part in error["loc"]:
                path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
            errors.append(f"{path or '提交'}：{reasons.get(error['type'], '字段格式或类型不正确')}")
        raise SubmissionError("提交格式不正确：" + "；".join(errors)) from None


def submit_tool(report: ProblemReport, cfg: dict, summary: bool = False) -> dict:
    """为当前报告生成严格 schema；不注册到现有查询工具或 AI 调用流程。"""
    p = cfg["checker"]
    disabled = set(report.disabled) | set(cfg["problems"]["disabled"])
    ordinary = [pid for pid, spec in PROBLEMS.items() if spec.category != "highlight" and pid not in disabled and pid != "other"]
    ordinary.append("other")
    highlights = [pid for pid, spec in PROBLEMS.items() if spec.category == "highlight" and pid not in disabled]
    schema = ReportSubmission.model_json_schema()
    definitions = schema["$defs"]

    def text(field, description, maximum):
        field.update(description=description, minLength=1, maxLength=maximum)

    for name, group, enum, minimum, maximum in (
        ("Finding", "finding", ordinary, 1, p["max_finding_targets"]),
        ("Pattern", "pattern", ordinary, 2, p["max_pattern_targets"]),
        ("Highlight", "highlight", highlights, 1, p["max_highlight_targets"]),
    ):
        props = definitions[name]["properties"]
        props["problem"].update(enum=enum, description="问题目录编号，必须与每个 target 的程序命中对应；other 表示 AI 判断、无程序依据。" if name != "Highlight" else "亮点编号，必须与每个 target 的亮点命中对应。")
        props["targets"].update(minItems=minimum, maxItems=maximum, uniqueItems=True,
            description=("只在这里填写跨场瞬间或回合编号，如 M3:R9-death、M3:R9。" if summary else
                         "只在这里填写瞬间编号、回合编号或 match；match 只能用于 spray_control。"),
            items={"type": "string", "pattern": SUMMARY_TARGET_PATTERN if summary else TARGET_PATTERN})
        text(props["analysis"], "结合情境说明原因和轻重；分析文字里不要写编号，编号只放在 targets 中。",
             p[f"max_{group}_analysis_length"])
        if name == "Finding":
            props["severity"]["description"] = "问题严重程度：high 高、medium 中、low 低。"
            text(props["training"], "针对本条问题提出具体、可执行的训练，不需要填写引用编号。", p["max_training_length"])
    props = schema["properties"]
    text(props["overview"], "整场总览，三到五句；数字和人数须来自摘要，不要写引用编号。", p["max_overview_length"])
    for name, minimum, maximum, description in (
        ("findings", 1, p["max_findings"], "问题结论，至少一条；只选择有程序命中的问题，或明确使用 other。"),
        ("patterns", 0, p["max_patterns"], "反复出现的问题；每条至少关联两个不同 target。"),
        ("highlights", 0, p["max_highlights"], "有数据依据的亮点，没有时提交空数组。"),
        ("training_plan", 1, p["max_training_plan_items"], "按优先级列出具体训练计划，至少一条。"),
    ):
        props[name].update(minItems=minimum, maxItems=maximum, description=description)
    text(props["training_plan"]["items"], "一条具体、可执行的训练安排。", p["max_training_plan_item_length"])
    return {"name": "submit_report", "description":
        "提交结构化复盘结论，程序会逐条核对编号、前提条件、数字和敏感说法。所有字段必填，没有规律或亮点时填写空数组。"
        "分析文字里不要写编号，编号只放在 targets 中；other 为 AI 判断，无程序依据，合计不超过 "
        f"{p['max_other']} 条。训练建议里的次数和时长不按比赛数据检查。",
        "input_schema": schema}
