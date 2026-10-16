"""多场结构化诊断：复用单场提交循环和检查器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cs2coach.ai.diagnose import DroppedItem, run_diagnosis_loop
from cs2coach.ai.providers import ChatProvider, Usage, make_provider
from cs2coach.features.problems import ProblemReport
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.summary.digest import build_summary_digest, summary_numbers
from cs2coach.summary.prompts import SUMMARY_INSTRUCTION, SUMMARY_PROMPT
from cs2coach.summary.queries import SUMMARY_TOOLS, run_summary_tool
from cs2coach.tools.checker import Violation, check_summary_submission
from cs2coach.tools.submission import ReportSubmission, submit_tool


@dataclass
class SummaryDiagnosis:
    submission: ReportSubmission
    digest: str
    provider: str
    model: str
    report: str = ""
    violations_history: list[list[Violation]] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    repairs: int = 0
    prompt_tokens_history: list[int] = field(default_factory=list)


def diagnose_summary(summary: PlayerSummary, cfg: dict, baseline: dict | None,
                     provider: ChatProvider | None = None) -> SummaryDiagnosis:
    digest = build_summary_digest(summary, baseline, cfg)
    numbers = summary_numbers(summary, baseline)
    provider = provider or make_provider(cfg)
    disabled = sorted({pid for match in summary.matches
                       for pid in summary.detail(match.number)[1].disabled})
    tool = submit_tool(ProblemReport(hits=[], disabled=disabled, skipped={},
                       baseline_source=baseline.get("source") if baseline else None), cfg,
                       summary=True)
    kills = sum(match.kills for match in summary.matches)
    deaths = sum(match.deaths for match in summary.matches)
    fallback = (f"共 {len(summary.matches)} 场，在场 {summary.rounds_played_total} 回合，"
                f"击杀 {kills} 次、死亡 {deaths} 次（AI 总览未通过检查，已替换）。")
    result = run_diagnosis_loop(provider, cfg, SUMMARY_PROMPT, digest, SUMMARY_INSTRUCTION,
        SUMMARY_TOOLS, tool,
        lambda sub: check_summary_submission(sub, summary, digest, cfg, numbers),
        lambda name, args: run_summary_tool(name, args, summary), fallback)
    diagnosis = SummaryDiagnosis(submission=result.submission, digest=digest,
        provider=provider.name, model=result.model, usage=result.usage,
        violations_history=result.violations_history, dropped=result.dropped,
        tool_calls=result.tool_calls, raw_calls=result.raw_calls, repairs=result.repairs,
        prompt_tokens_history=result.prompt_tokens_history)
    from cs2coach.summary.render_report import render_summary_diagnosis
    diagnosis.report = render_summary_diagnosis(diagnosis, summary, baseline)
    return diagnosis
