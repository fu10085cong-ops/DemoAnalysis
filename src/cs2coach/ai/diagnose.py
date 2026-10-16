"""结构化诊断：AI 选取和讲解，程序检查提交并生成可追溯的报告。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from cs2coach.ai.prompts import SYSTEM_PROMPT, USER_INSTRUCTION
from cs2coach.ai.providers import ChatProvider, ToolResult, Usage, make_provider
from cs2coach.features.models import MatchMoments
from cs2coach.features.problems import FrequencyRow, ProblemReport, detect_problems, problem_frequency
from cs2coach.parsing.match import MatchData
from cs2coach.tools.checker import Violation, check_submission
from cs2coach.tools.digest import build_digest, match_score, whole_match_numbers
from cs2coach.tools.queries import TOOLS, ToolError, run_tool
from cs2coach.tools.submission import ReportSubmission, SubmissionError, parse_submission, submit_tool


class DiagnosisError(Exception):
    """诊断失败。audit 记录失败前的违规历史与原始提交，CLI 写入 submission_failed.json 供排查。"""

    def __init__(self, message: str, audit: dict[str, Any] | None = None):
        super().__init__(message)
        self.audit = audit


class DroppedItem(BaseModel):
    path: str
    content: dict[str, Any] | str
    violations: list[Violation]
    action: Literal["removed", "replaced"] = "removed"


@dataclass
class Diagnosis:
    submission: ReportSubmission
    problems: ProblemReport
    frequency: list[FrequencyRow]
    digest: str
    provider: str
    model: str
    score: tuple[int, int]
    report: str = ""
    violations_history: list[list[Violation]] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    repairs: int = 0
    prompt_tokens_history: list[int] = field(default_factory=list)
    rendered_submission: dict[str, Any] = field(default_factory=dict)


def _error_text(violations: list[Violation]) -> str:
    groups: dict[str, list[Violation]] = {}
    for v in violations:
        groups.setdefault(v.path, []).append(v)
    lines = ["提交未通过数据检查："]
    for path, entries in groups.items():
        lines.append(f"{path}：")
        lines.extend(f"- [{v.code}] {v.message}" for v in entries)
    return "\n".join([*lines, "请修改后再次调用 submit_report，分析文字中的编号请移到 targets。"])


def _submission_args(args: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    """兼容模型把报告多包一层对象；保留原始参数供审计。"""
    if args is not None and len(args) == 1:
        key, value = next(iter(args.items()))
        if isinstance(value, str):  # DeepSeek 常把整份报告转成 JSON 字符串再包一层：{"report": "{\"overview\": ...}"}
            try:
                value = json.loads(value)
            except ValueError:
                return args, None
        if isinstance(value, dict) and set(value).intersection(ReportSubmission.model_fields):
            return value, key
    return args, None


def _schema_violations(sub: ReportSubmission, schema: dict) -> list[Violation]:
    """兼容服务未强制 schema 时，在本地核对工具已经声明的格式约束。"""
    violations = []

    def add(path, message):
        violations.append(Violation(path=path, code="invalid_submission", message=message))

    def walk(value, rule, path):
        if "$ref" in rule:
            rule = schema["$defs"][rule["$ref"].split("/")[-1]]
        if "enum" in rule and value not in rule["enum"]:
            add(path, f"{value!r} 不在当前工具允许的编号或取值中。")
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, rule["properties"][key], f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            if len(value) < rule.get("minItems", 0):
                add(path, f"至少需要 {rule['minItems']} 项，实际为 {len(value)} 项。")
            maximum = rule.get("maxItems", len(value))
            for i, child in enumerate(value):
                item_path = f"{path}[{i}]"
                if i >= maximum:
                    add(item_path, f"超过当前工具允许的 {maximum} 项上限。")
                if rule.get("uniqueItems") and child in value[:i]:
                    add(item_path, "targets 不允许重复编号。")
                walk(child, rule["items"], item_path)
        elif isinstance(value, str):
            if len(value) < rule.get("minLength", 0):
                add(path, "文字不能为空。")
            if len(value) > rule.get("maxLength", len(value)):
                add(path, f"文字超过当前工具允许的 {rule['maxLength']} 字符上限。")
            if "pattern" in rule and re.search(rule["pattern"], value) is None:
                add(path, f"编号 {value!r} 不符合工具规定的格式。")

    walk(sub.model_dump(), schema, "")
    return violations


def _prune(sub: ReportSubmission, violations: list[Violation], fallback_overview: str,
           cfg: dict) -> tuple[ReportSubmission, list[DroppedItem]]:
    """按原始下标删除整条结论，原文及原因留下审计记录。"""
    result = sub.model_copy(deep=True)
    bad: dict[tuple[str, int], list[Violation]] = {}
    for v in violations:
        match = re.match(r"^(findings|patterns|highlights)\[(\d+)\]", v.path)
        if match:
            bad.setdefault((match[1], int(match[2])), []).append(v)
    # too_many_other 是跨组违规，保留顺序靠前且没有其他违规的允许数量。
    excess = [v for v in violations if v.code == "too_many_other"]
    if excess:
        remaining = [(group, i) for group in ("findings", "patterns", "highlights")
                     for i, entry in enumerate(getattr(sub, group))
                     if entry.problem == "other" and (group, i) not in bad]
        for key in remaining[cfg["checker"]["max_other"]:]:
            bad.setdefault(key, []).extend(excess)
    dropped = []
    for group in ("findings", "patterns", "highlights"):
        kept = []
        for i, entry in enumerate(getattr(sub, group)):
            reasons = bad.get((group, i))
            if reasons:
                dropped.append(DroppedItem(path=f"{group}[{i}]", content=entry.model_dump(), violations=reasons))
            else:
                kept.append(entry)
        setattr(result, group, kept)
    overview = [v for v in violations if v.path == "overview"]
    if overview:
        dropped.append(DroppedItem(path="overview", content=sub.overview, violations=overview, action="replaced"))
        result.overview = fallback_overview
    if not result.findings:
        raise DiagnosisError("删除违规结论后没有任何 finding，无法生成有依据的报告")
    return result, dropped


@dataclass
class LoopResult:
    submission: ReportSubmission
    dropped: list[DroppedItem]
    violations_history: list[list[Violation]]
    raw_calls: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    usage: Usage
    repairs: int
    model: str
    prompt_tokens_history: list[int]


def run_diagnosis_loop(provider: ChatProvider, cfg: dict, system_prompt: str, digest: str,
                       instruction: str, tools: list[dict], submission_tool: dict,
                       check, query, fallback_overview: str) -> LoopResult:
    """单场和多场共用提交、检查、退回、删除、审计流程；失败时把审计挂在 DiagnosisError 上。"""
    state: dict[str, Any] = {}
    try:
        return _run_loop(provider, cfg, system_prompt, digest, instruction, tools, submission_tool,
                         check, query, fallback_overview, state)
    except DiagnosisError as exc:
        if exc.audit is None:
            latest = state.get("latest")
            exc.audit = {
                "error": str(exc),
                "latest_submission": latest.model_dump() if latest is not None else None,
                "violations_history": [[v.model_dump() for v in group] for group in state.get("history", [])],
                "raw_calls": state.get("raw_calls", []),
                "prompt_tokens_history": state.get("prompt_tokens_history", []),
            }
        raise


def _run_loop(provider, cfg, system_prompt, digest, instruction, tools, submission_tool,
              check, query, fallback_overview, state) -> LoopResult:
    ac = cfg["ai"]
    provider.start(system_prompt, [digest, instruction], [*tools, submission_tool])
    usage, calls, history = Usage(), [], []
    raw_calls: list[dict[str, Any]] = []
    repairs = 0
    prompt_tokens_history: list[int] = []
    query_count = 0
    query_limit = ac.get(provider.name, {}).get("max_tool_calls")
    queries_exhausted = False
    empty_streak = 0
    model = provider.model
    latest: ReportSubmission | None = None
    latest_violations: list[Violation] = []
    state.update(history=history, raw_calls=raw_calls, prompt_tokens_history=prompt_tokens_history)

    def finish(submission, dropped=None):
        return LoopResult(submission, dropped or [], history, raw_calls, calls, usage, repairs,
                          model, prompt_tokens_history)

    for _ in range(int(ac["max_turns"])):
        turn = provider.send()
        usage.add(turn.usage)
        prompt_tokens_history.append(turn.usage.input_tokens)
        model = turn.model or model
        if turn.stop == "refusal":
            raise DiagnosisError(f"模型拒绝生成（{turn.detail}）")
        if turn.stop == "max_tokens":
            raise DiagnosisError("输出超过 max_tokens，请在配置中调大对应模型的 max_tokens")
        if turn.stop == "error":
            raise DiagnosisError(f"模型服务异常结束（{turn.detail}），请稍后重试")

        submitted = False
        empty_submission = False
        candidate = None
        violations: list[Violation] = []
        if turn.stop == "tool_use":
            results = []
            submit_count = sum(c.name == "submit_report" for c in turn.tool_calls)
            for call in turn.tool_calls:
                if call.name == "submit_report":
                    submitted = True
                    args, unwrapped = _submission_args(call.args)
                    raw_call = {"raw_args": call.raw_args, "parsed_args": call.args}
                    if unwrapped is not None:
                        raw_call["unwrapped"] = unwrapped
                    raw_calls.append(raw_call)
                    if submit_count > 1:
                        violations = [Violation(path="submission", code="multiple_submissions",
                            message="同一条回复只能调用一次 submit_report；收到失败结果后才可修改并重新提交。")]
                    else:
                        empty_submission = (args is not None and
                            not set(args).intersection(ReportSubmission.model_fields))
                        empty_streak = empty_streak + 1 if empty_submission else 0
                        try:
                            if empty_submission:
                                raise SubmissionError("提交内容为空，请提交完整对象")
                            if args is None:
                                raise SubmissionError("提交参数不是合法的 JSON 对象")
                            candidate = parse_submission(args)
                            violations = (_schema_violations(candidate, submission_tool["input_schema"])
                                          + check(candidate))
                            latest, latest_violations = candidate, violations
                            state["latest"] = latest
                        except SubmissionError as exc:
                            violations = [Violation(path="submission", code="invalid_submission", message=str(exc))]
                    err = bool(violations)
                    out = _error_text(violations) if err else "提交已通过数据检查。"
                elif query_limit is not None and query_count >= query_limit:
                    out, err = "查询次数已用完，请直接调用 submit_report。", True
                elif call.args is None:
                    query_count += 1
                    out, err = f"参数不是合法的 JSON 对象：{call.raw_args!r}", True
                else:
                    query_count += 1
                    try:
                        out, err = query(call.name, call.args), False
                    except ToolError as exc:
                        out, err = str(exc), True
                calls.append({"name": call.name, "input": call.args, "error": err})
                results.append(ToolResult(id=call.id, content=out, is_error=err))
            provider.add_tool_results(results)
            if query_limit is not None and query_count >= query_limit and not queries_exhausted:
                queries_exhausted = True
                provider.add_user("查询次数已用完，请直接提交完整报告，不要再调用查询工具。")
                provider.limit_to_submission()
            if not submitted:
                continue
            history.append(violations)
            if candidate is not None and not violations:
                return finish(candidate)
        else:
            empty_streak = 0
            violations = [Violation(path="submission", code="submission_required",
                message="必须调用 submit_report 提交结构化结论，不能以自由文本结束。")]
            history.append(violations)

        if empty_submission and empty_streak <= 2:
            continue
        if repairs < int(ac["max_repairs"]):
            repairs += 1
            if not submitted:
                provider.add_user(violations[0].message + "请现在调用 submit_report。")
            continue
        if latest is None:
            raise DiagnosisError("达到修正上限仍没有可检查的结构化提交，请通过 submit_report 提交合法参数")
        final, dropped = _prune(latest, latest_violations, fallback_overview, cfg)
        remaining = (_schema_violations(final, submission_tool["input_schema"])
                     + check(final))
        if remaining:
            raise DiagnosisError("删除违规结论后仍未通过检查：" + _error_text(remaining))
        return finish(final, dropped)
    raise DiagnosisError(f"超过 {ac['max_turns']} 轮仍未完成报告")


def diagnose(match: MatchData, mm: MatchMoments, cfg: dict[str, Any], baseline: dict[str, Any] | None,
             provider: ChatProvider | None = None) -> Diagnosis:
    problems = detect_problems(mm, baseline, cfg)
    frequency = problem_frequency(problems, mm, baseline, cfg)
    digest = build_digest(match, mm, baseline, problems, frequency, cfg)
    score = match_score(match, mm.player)
    whole_numbers = whole_match_numbers(mm, baseline, frequency, score)
    provider = provider or make_provider(cfg)
    submission_tool = submit_tool(problems, cfg)
    kills = sum(m.kind == "kill" for m in mm.moments)
    deaths = sum(m.kind == "death" for m in mm.moments)
    fallback = ("本场比分 {match.score}，击杀 {count.kills} 次、死亡 {count.deaths} 次"
                "（AI 总览未通过检查，已替换）。")
    result = run_diagnosis_loop(provider, cfg, SYSTEM_PROMPT, digest, USER_INSTRUCTION,
        TOOLS, submission_tool,
        lambda candidate: check_submission(candidate, mm, problems, digest, cfg, whole_numbers,
                                           score, frequency, baseline),
        lambda name, args: run_tool(name, args, match, mm), fallback)
    d = Diagnosis(submission=result.submission, problems=problems, frequency=frequency,
        digest=digest, provider=provider.name, model=result.model, score=score, usage=result.usage,
        tool_calls=result.tool_calls, raw_calls=result.raw_calls, repairs=result.repairs,
        violations_history=result.violations_history, dropped=result.dropped,
        prompt_tokens_history=result.prompt_tokens_history)
    from cs2coach.report.render import render_diagnosis
    d.report = render_diagnosis(d, mm, {sid: p.name for sid, p in match.players.items()}, cfg, baseline)
    return d
