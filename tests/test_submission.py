"""提交格式、动态工具 schema 与中文结构错误说明。"""

import copy
import json
import re
from pathlib import Path

import pytest

from cs2coach.config import load_config
from cs2coach.features.problems import PROBLEMS, ProblemReport
from cs2coach.tools.submission import SubmissionError, parse_submission, submit_tool

FIXTURES = Path(__file__).parent / "fixtures"


def submission():
    return json.loads((FIXTURES / "mirage_submission_valid.json").read_text(encoding="utf-8"))


def report():
    return ProblemReport(hits=[], disabled=["died_without_seeing", "slow_reaction", "good_trade"],
                         skipped={}, baseline_source="测试样本")


def resolve(schema, root):
    if "$ref" in schema:
        return root["$defs"][schema["$ref"].split("/")[-1]]
    return schema


def test_schema_is_strict_at_every_object_and_all_fields_required():
    schema = submit_tool(report(), load_config())["input_schema"]
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    assert set(schema["properties"]) == {"overview", "findings", "patterns", "highlights", "training_plan"}


def test_enums_follow_disabled_ids_and_categories():
    cfg = load_config()
    cfg["problems"]["disabled"].append("poor_crosshair_placement")
    tool = submit_tool(report(), cfg)
    defs = tool["input_schema"]["$defs"]
    ordinary = defs["Finding"]["properties"]["problem"]["enum"]
    assert "other" in ordinary and "slow_reaction" not in ordinary
    assert "died_without_seeing" not in ordinary and "poor_crosshair_placement" not in ordinary
    assert defs["Pattern"]["properties"]["problem"]["enum"] == ordinary
    highlights = defs["Highlight"]["properties"]["problem"]["enum"]
    assert set(highlights) == {pid for pid, spec in PROBLEMS.items() if spec.category == "highlight" and pid != "good_trade"}
    assert not set(ordinary).intersection(highlights)
    assert "分析文字里不要写编号" in tool["description"]


def test_schema_limits_follow_configuration():
    cfg = load_config()
    cfg["checker"].update(max_findings=2, max_patterns=1, max_highlights=1,
                          max_finding_analysis_length=70, max_training_length=30,
                          max_finding_targets=4, max_training_plan_items=2)
    schema = submit_tool(report(), cfg)["input_schema"]
    assert schema["properties"]["findings"]["minItems"] == 1
    assert schema["properties"]["findings"]["maxItems"] == 2
    assert schema["properties"]["patterns"]["minItems"] == 0
    assert schema["properties"]["patterns"]["maxItems"] == 1
    assert schema["properties"]["highlights"]["maxItems"] == 1
    assert schema["properties"]["training_plan"]["maxItems"] == 2
    finding = schema["$defs"]["Finding"]["properties"]
    assert finding["analysis"]["maxLength"] == 70 and finding["training"]["maxLength"] == 30
    assert finding["targets"]["maxItems"] == 4 and finding["targets"]["minItems"] == 1
    assert schema["$defs"]["Pattern"]["properties"]["targets"]["minItems"] == 2
    assert schema["$defs"]["Highlight"]["properties"]["targets"]["maxItems"] == 3


@pytest.mark.parametrize("target,valid", [("R5", True), ("R5-death", True), ("R5-kill1", True),
    ("R5-assist2", True), ("match", True), ("R5-kill", False), ("[[R5]]", False), ("R5-foo", False)])
def test_target_pattern(target, valid):
    schema = submit_tool(report(), load_config())["input_schema"]
    pattern = schema["$defs"]["Finding"]["properties"]["targets"]["items"]["pattern"]
    assert bool(re.fullmatch(pattern, target)) == valid


@pytest.mark.parametrize("mutation,word", [
    (lambda d: d.pop("overview"), "缺少必填字段"),
    (lambda d: d.update(extra="禁止"), "不允许额外字段"),
    (lambda d: d["findings"][0].update(analysis=123), "应为字符串"),
    (lambda d: d["findings"][0].update(severity="critical"), "取值不在允许范围内"),
    (lambda d: d.update(findings={}), "应为数组"),
    (lambda d: d["findings"][0].update(extra=True), "findings[0].extra"),
])
def test_parse_errors_are_chinese_and_locate_the_field(mutation, word):
    args = submission()
    mutation(args)
    with pytest.raises(SubmissionError, match=re.escape(word)):
        parse_submission(args)


def test_parse_is_structural_and_does_not_load_config(monkeypatch):
    monkeypatch.setattr("cs2coach.config.load_config", lambda *a: pytest.fail("解析应为纯函数"))
    args = submission()
    # 语义问题交给检查器；长度和动态编号交给 schema。
    args["findings"][0].update(problem="unknown", targets=["R99-death"], analysis="长" * 2000)
    assert parse_submission(args).findings[0].problem == "unknown"


def test_schema_contract_without_adding_jsonschema_dependency():
    # 环境未安装 jsonschema，按任务要求直接检查合法值与非法值涉及的 schema 结构。
    base_report = report()
    base_report.disabled = []
    schema = submit_tool(base_report, load_config())["input_schema"]
    args = submission()
    assert parse_submission(args).model_dump() == args
    for group in ("findings", "patterns", "highlights"):
        array = schema["properties"][group]
        assert array["minItems"] <= len(args[group]) <= array["maxItems"]
        item = resolve(array["items"], schema)["properties"]
        for entry in args[group]:
            assert entry["problem"] in item["problem"]["enum"]
            assert item["targets"]["minItems"] <= len(entry["targets"]) <= item["targets"]["maxItems"]
            assert all(re.fullmatch(item["targets"]["items"]["pattern"], t) for t in entry["targets"])
            assert len(entry["analysis"]) <= item["analysis"]["maxLength"]
        assert "invented_problem" not in item["problem"]["enum"]
        assert not re.fullmatch(item["targets"]["items"]["pattern"], "R5-unknown")
        assert item["analysis"]["maxLength"] < len("长" * (item["analysis"]["maxLength"] + 1))
