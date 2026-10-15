"""AI 诊断：摘要放进提示词，配两个查询工具（方案 C，2026-10-15 产品负责人确定）。

诊断循环与具体模型无关（ai/providers.py）；手写循环而不是用各家 SDK 的工具运行器，
因为要在循环里检查引用、必要时要求修正。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cs2coach.ai.prompts import (
    BARE_INSTRUCTION,
    NO_CITATION_INSTRUCTION,
    REPAIR_INSTRUCTION,
    REPAIR_SUFFIX,
    SYSTEM_PROMPT,
    USER_INSTRUCTION,
)
from cs2coach.ai.providers import ChatProvider, ToolResult, Usage, make_provider
from cs2coach.features.models import MatchMoments
from cs2coach.parsing.match import MatchData
from cs2coach.tools.citations import CitationCheck, check_citations, render_report
from cs2coach.tools.digest import build_digest
from cs2coach.tools.queries import TOOLS, ToolError, run_tool


class DiagnosisError(Exception):
    pass


@dataclass
class Diagnosis:
    report: str  # 渲染后的 Markdown（引用已转成〔编号〕，附跳转命令）
    raw_text: str  # 模型原文
    citations: CitationCheck
    digest: str
    provider: str
    model: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    repairs: int = 0


def diagnose(match: MatchData, mm: MatchMoments, cfg: dict[str, Any], baseline: dict[str, Any] | None,
             provider: ChatProvider | None = None) -> Diagnosis:
    ac = cfg["ai"]
    provider = provider or make_provider(cfg)
    digest = build_digest(match, mm, baseline)
    provider.start(SYSTEM_PROMPT, [digest, USER_INSTRUCTION], TOOLS)

    result = Diagnosis(report="", raw_text="", citations=CitationCheck([], []), digest=digest,
                       provider=provider.name, model=provider.model)
    for _ in range(int(ac["max_turns"])):
        turn = provider.send()
        result.usage.add(turn.usage)
        result.model = turn.model or result.model
        if turn.stop == "refusal":
            raise DiagnosisError(f"模型拒绝生成（{turn.detail}）")
        if turn.stop == "max_tokens":
            raise DiagnosisError("输出超过 max_tokens，请在配置中调大对应模型的 max_tokens")
        if turn.stop == "error":
            raise DiagnosisError(f"模型服务异常结束（{turn.detail}），请稍后重试")

        if turn.stop == "tool_use":
            results = []
            for call in turn.tool_calls:
                if call.args is None:
                    out, err = f"参数不是合法的 JSON 对象：{call.raw_args!r}", True
                else:
                    try:
                        out, err = run_tool(call.name, call.args, match, mm), False
                    except ToolError as e:
                        out, err = str(e), True
                result.tool_calls.append({"name": call.name, "input": call.args, "error": err})
                results.append(ToolResult(id=call.id, content=out, is_error=err))
            provider.add_tool_results(results)
            continue

        check = check_citations(turn.text, mm)
        if check.ok or result.repairs >= int(ac["max_repairs"]):
            result.raw_text, result.citations = turn.text, check
            result.report = render_report(turn.text, mm)
            return result
        result.repairs += 1
        fixes = []
        if check.invalid:
            fixes.append(REPAIR_INSTRUCTION.format(invalid="、".join(check.invalid)))
        if check.bare:
            fixes.append(BARE_INSTRUCTION.format(bare="、".join(check.bare)))
        provider.add_user("\n".join([*fixes, REPAIR_SUFFIX]) if fixes else NO_CITATION_INSTRUCTION)
    raise DiagnosisError(f"超过 {ac['max_turns']} 轮仍未完成报告")
