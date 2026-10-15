"""多场汇总只暴露瞬间与问题命中查询，不保留 MatchData。"""

from __future__ import annotations

import json
from typing import Any

from cs2coach.features.problems import PROBLEMS
from cs2coach.report.evidence import evidence_sentence
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.summary.digest import problem_hits
from cs2coach.tools.queries import ToolError


SUMMARY_TOOLS: list[dict[str, Any]] = [
    {"name": "get_moment", "description": "查看指定场次的决定性瞬间完整数据与该场的问题命中。",
     "input_schema": {"type": "object", "properties": {
         "match": {"type": "string", "description": "场次编号，如 M3"},
         "moment_id": {"type": "string", "description": "瞬间编号，如 R9-death"}},
         "required": ["match", "moment_id"], "additionalProperties": False}},
    {"name": "list_problem_hits", "description": "列出指定问题在全部场次的命中和简短依据。",
     "input_schema": {"type": "object", "properties": {
         "problem": {"type": "string", "enum": list(PROBLEMS)}},
         "required": ["problem"], "additionalProperties": False}},
]


def run_summary_tool(name: str, args: dict[str, Any], summary: PlayerSummary) -> str:
    if name == "get_moment":
        number, moment_id = args.get("match"), args.get("moment_id")
        if not isinstance(number, str) or not isinstance(moment_id, str):
            raise ToolError("match 和 moment_id 应为字符串")
        if number not in {m.number for m in summary.matches}:
            raise ToolError(f"没有场次 {number!r}；可用场次：{', '.join(m.number for m in summary.matches)}")
        mm, report = summary.detail(number)
        moment = next((m for m in mm.moments if m.id == moment_id), None)
        if moment is None:
            raise ToolError(f"{number} 没有编号为 {moment_id!r} 的瞬间；可用编号："
                            + ", ".join(m.id for m in mm.moments))
        return json.dumps({"target": f"{number}:{moment_id}",
            "moment": moment.model_dump(exclude={"player", "jump_command"}),
            "problems": [h.model_dump() for h in report.hits if h.target == moment_id]},
            ensure_ascii=False, allow_nan=False)
    if name == "list_problem_hits":
        problem = args.get("problem")
        if not isinstance(problem, str) or problem not in PROBLEMS:
            raise ToolError(f"problem 应为已登记的问题编号；收到 {problem!r}")
        return json.dumps([{"target": ref, "evidence": evidence_sentence(hit)}
                           for ref, hit in problem_hits(summary, problem)], ensure_ascii=False)
    raise ToolError(f"没有名为 {name} 的工具")
