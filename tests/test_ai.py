"""摘要、查询、结构化诊断及服务适配：全部使用假客户端，不调用真实 API。"""

import copy
import json
from types import SimpleNamespace

import pytest

from cs2coach.ai.claude import ClaudeProvider
from cs2coach.ai.diagnose import DiagnosisError, diagnose
from cs2coach.ai.openai_compat import OpenAICompatProvider
from cs2coach.ai.providers import ProviderError, make_provider
from cs2coach.baseline import build_baseline, compare
from cs2coach.config import load_config
from cs2coach.features.problems import detect_problems, problem_frequency
from cs2coach.tools.checker import check_submission
from cs2coach.tools.digest import build_digest
from cs2coach.tools.queries import TOOLS, ToolError, get_moment, get_round, run_tool
from tests.helpers.synthetic import A, C, D
from tests.test_moments import find_moments, scenario

CFG = load_config()
BASELINE = build_baseline({"time_to_damage_ms": [300, 350, 400, 450], "counter_strafing_pct": [70, 80, 90]}, "测试样本")


@pytest.fixture
def match():
    return scenario()


@pytest.fixture
def mm(match):
    return find_moments(match, A, CFG)


def payload(**overrides):
    return dict(overview="本场应重点复盘接敌准备。训练围绕首枪稳定性展开。下次复盘继续观察变化。",
        findings=[dict(problem="other", targets=["R1-death"], severity="low",
                       analysis="这次交火的准备值得复盘，具体细节应结合上下文判断。", training="每天练100次目标切换。")],
        patterns=[], highlights=[], training_plan=["每天练15分钟。"] ) | overrides


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def claude_resp(blocks, stop):
    return SimpleNamespace(content=blocks, stop_reason=stop, model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=20, output_tokens=50, cache_read_input_tokens=80, cache_creation_input_tokens=0))


def submit_response(args=None):
    return claude_resp([tool_block("submit_report", payload() if args is None else args)], "tool_use")


class FakeAnthropic:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(copy.copy(kwargs) | {"messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def claude(responses, cfg=CFG):
    fake = FakeAnthropic(responses)
    return ClaudeProvider(cfg["ai"]["claude"], fake), fake


def test_compare_respects_metric_direction():
    c = compare("time_to_damage_ms", 360, BASELINE)
    assert c["pro_median"] == 375 and c["pro_n"] == 4 and c["better_than_pro_pct"] == 50
    assert compare("time_to_damage_ms", 250, BASELINE)["better_than_pro_pct"] == 100
    assert compare("counter_strafing_pct", 85, BASELINE)["better_than_pro_pct"] == 67
    assert compare("counter_strafing_pct", None, BASELINE) is None
    assert compare("spray_accuracy_pct", 30, BASELINE) is None


def test_digest_lists_moments_and_new_sections_before_them(match, mm):
    problems = detect_problems(mm, BASELINE, CFG)
    frequency = problem_frequency(problems, mm, BASELINE, CFG)
    text = build_digest(match, mm, BASELINE, problems, frequency)
    for m in mm.moments:
        assert f"[[{m.id}]]" in text
    assert "## 比赛" in text and "## 整场枪法" in text and "测试样本" in text
    assert "R1T" in text and "[[R1]]" in text and "道具：烟" in text
    assert text.index("## 问题频率") < text.index("## 决定性瞬间")
    assert text.index("## 检测到的问题") < text.index("## 决定性瞬间")
    assert "报告应优先讲突出问题" in text and "missed_trade" in text


def test_tool_schemas_are_strict_compatible():
    for t in TOOLS:
        assert t["input_schema"]["additionalProperties"] is False and t["input_schema"]["required"]


def test_get_moment(mm):
    assert json.loads(get_moment("R1-death", mm))["opponent"] == D
    with pytest.raises(ToolError, match="R1-kill1"):
        get_moment("R9-kill1", mm)


def test_get_round(match, mm):
    data = json.loads(get_round(1, match, mm))
    assert data["player_side"] == "T" and data["winner"] == "T" and data["player_won"] is True
    assert [k["victim"] for k in data["kills"]] == ["carol", "bob", "alice"]
    assert data["player_moments"] == ["R1-kill1", "R1-death"]
    with pytest.raises(ToolError):
        get_round(5, match, mm)


def test_run_tool_errors(match, mm):
    with pytest.raises(ToolError):
        run_tool("delete_everything", {}, match, mm)
    with pytest.raises(ToolError):
        run_tool("get_round", {"round": "abc"}, match, mm)


def test_first_submission_passes_without_repairs(match, mm):
    provider, fake = claude([submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 0 and d.violations_history == [[]] and d.dropped == []
    assert d.submission.model_dump() == payload() and "AI 判断，无程序依据" in d.report
    assert check_submission(d.submission, mm, d.problems, d.digest, CFG) == []
    assert d.usage.input_tokens == 100 and d.usage.cache_read_tokens == 80
    assert {t["name"] for t in fake.calls[0]["tools"]} == {"get_moment", "get_round", "submit_report"}
    assert not hasattr(d, "citations") and not hasattr(d, "raw_text")
    assert provider.messages[-1]["content"][0]["is_error"] is False


def test_first_violation_then_valid_submission(match, mm):
    bad = payload()
    bad["findings"][0]["analysis"] = "首次命中花了987654321毫秒。"
    provider, fake = claude([submit_response(bad), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 1 and len(d.violations_history) == 2
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "findings[0].analysis" in result["content"] and "987654321" in result["content"]


def test_repeated_violation_drops_whole_finding_and_keeps_valid_one(match, mm):
    bad = payload()
    wrong = copy.deepcopy(bad["findings"][0])
    wrong["targets"] = ["R99-death"]
    bad["findings"].append(wrong)
    provider, _ = claude([submit_response(bad)] * 3)
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 2 and len(d.violations_history) == 3 and len(d.submission.findings) == 1
    assert d.dropped[0].path == "findings[1]" and d.dropped[0].content["targets"] == ["R99-death"]
    assert "unknown_target" in {v.code for v in d.dropped[0].violations}
    assert "有 1 条结论未通过数据检查，已删除" in d.report
    assert "R99-death" not in d.report
    assert check_submission(d.submission, mm, d.problems, d.digest, CFG) == []


def test_all_findings_removed_is_error(match, mm):
    bad = payload()
    bad["findings"][0]["targets"] = ["R99-death"]
    with pytest.raises(DiagnosisError, match="没有任何 finding"):
        diagnose(match, mm, CFG, BASELINE, claude([submit_response(bad)] * 3)[0])


def test_overview_is_replaced_with_program_score_kills_deaths(match, mm):
    bad = payload(overview="本场987654321回合。")
    d = diagnose(match, mm, CFG, BASELINE, claude([submit_response(bad)] * 3)[0])
    assert d.repairs == 2 and "比分 1:0，击杀 1 次、死亡 1 次" in d.submission.overview
    assert "AI 总览未通过检查，已替换" in d.report
    assert d.dropped[0].action == "replaced" and d.dropped[0].content == bad["overview"]
    assert check_submission(d.submission, mm, d.problems, d.digest, CFG) == []


def test_ends_without_submission_gets_requested_to_submit(match, mm):
    provider, fake = claude([claude_resp([text_block("自由文本不能作为报告")], "end_turn"), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 1 and "必须调用 submit_report" in fake.calls[1]["messages"][-1]["content"]
    assert "自由文本不能作为报告" not in d.report


@pytest.mark.parametrize("response", [submit_response({"overview": "缺字段。"}), claude_resp([], "end_turn")])
def test_never_produces_checkable_submission_is_error(response, match, mm):
    with pytest.raises(DiagnosisError, match="没有可检查"):
        diagnose(match, mm, CFG, BASELINE, claude([response] * 3)[0])


def test_structurally_invalid_arguments_are_returned_in_chinese(match, mm):
    provider, fake = claude([submit_response({"overview": "缺字段。"}), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 1 and d.violations_history[0][0].code == "invalid_submission"
    error = fake.calls[1]["messages"][-1]["content"][0]
    assert error["is_error"] and "缺少必填字段" in error["content"]


def test_empty_submissions_do_not_consume_two_repairs_and_raw_calls_are_saved(match, mm):
    provider, fake = claude([submit_response({}), submit_response({"report": "旧格式"}), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 0 and len(d.violations_history) == 3
    assert all(v[0].message == "提交内容为空，请提交完整对象" for v in d.violations_history[:2])
    assert d.raw_calls[0] == {"raw_args": "{}", "parsed_args": {}}
    assert d.raw_calls[1]["parsed_args"] == {"report": "旧格式"}
    assert "提交内容为空，请提交完整对象" in fake.calls[1]["messages"][-1]["content"][0]["content"]


@pytest.mark.parametrize("wrapper", ["report", "submission", "result"])
def test_single_wrapper_is_unwrapped_without_repair(wrapper, match, mm):
    wrapped = {wrapper: payload()}
    provider, fake = claude([submit_response(wrapped)])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 0 and d.violations_history == [[]]
    assert d.submission.model_dump() == payload()
    assert d.raw_calls[0] == {"raw_args": json.dumps(wrapped, ensure_ascii=False),
                              "parsed_args": wrapped, "unwrapped": wrapper}
    assert provider.messages[-1]["content"][0]["is_error"] is False
    assert len(fake.calls) == 1


def test_partial_wrapper_is_checked_as_partial_submission(match, mm):
    wrapped = {"report": {"overview": "总览。"}}
    provider, fake = claude([submit_response(wrapped), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 1 and d.raw_calls[0]["unwrapped"] == "report"
    error = fake.calls[1]["messages"][-1]["content"][0]["content"]
    assert "缺少必填字段" in error and "提交内容为空" not in error
    assert "report：不允许额外字段" not in error


def test_two_top_level_keys_are_not_unwrapped(match, mm):
    wrapped = {"report": payload(), "note": "附注"}
    provider, fake = claude([submit_response(wrapped), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 0 and "unwrapped" not in d.raw_calls[0]
    assert d.raw_calls[0]["parsed_args"] == wrapped
    assert "提交内容为空，请提交完整对象" in fake.calls[1]["messages"][-1]["content"][0]["content"]


def test_third_consecutive_empty_submission_counts_as_repair(match, mm):
    provider, _ = claude([submit_response({}), submit_response({}), submit_response({}), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 1 and len(d.raw_calls) == 4
    assert all(v[0].code == "invalid_submission" for v in d.violations_history[:3])


@pytest.mark.parametrize("mutation,expected", [
    (lambda args: args.update(findings=[]), "findings"),
    (lambda args: args["findings"][0].update(targets=[]), "targets"),
    (lambda args: args["findings"][0].update(training="长" * 601), "training"),
    (lambda args: args["findings"][0].update(problem="weapon_not_ready"), "problem"),
])
def test_tool_schema_is_also_checked_locally(mutation, expected, match, mm):
    bad = payload()
    mutation(bad)
    provider, fake = claude([submit_response(bad), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 1 and d.violations_history[0][0].code == "invalid_submission"
    assert expected in fake.calls[1]["messages"][-1]["content"][0]["content"]


def test_zero_findings_cannot_survive_repair_limit(match, mm):
    with pytest.raises(DiagnosisError, match="没有任何 finding"):
        diagnose(match, mm, CFG, BASELINE, claude([submit_response(payload(findings=[]))] * 3)[0])


def test_query_then_submit_preserves_provider_history_and_usage(match, mm):
    provider, fake = claude([claude_resp([tool_block("get_moment", {"moment_id": "R1-death"})], "tool_use"), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 0 and [t["name"] for t in d.tool_calls] == ["get_moment", "submit_report"]
    assert d.usage.input_tokens == 200 and d.usage.cache_read_tokens == 160
    first, second = fake.calls
    assert first["thinking"] == {"type": "adaptive"} and first["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default" and all(t["strict"] for t in first["tools"])
    assert second["messages"][1]["role"] == "assistant"
    result = second["messages"][2]["content"][0]
    assert result["tool_use_id"] == "t1" and json.loads(result["content"])["id"] == "R1-death"


def test_query_error_is_reported_without_counting_as_submission_repair(match, mm):
    provider, fake = claude([claude_resp([tool_block("get_moment", {"moment_id": "R99-death"})], "tool_use"), submit_response()])
    d = diagnose(match, mm, CFG, BASELINE, provider)
    assert d.repairs == 0 and d.tool_calls[0]["error"] is True
    assert fake.calls[1]["messages"][-1]["content"][0]["is_error"]


def test_patterns_and_highlights_with_violations_are_removed_whole(match, mm):
    bad = payload(patterns=[dict(problem="other", targets=["R1-death", "R99-death"], analysis="值得复盘。")],
                  highlights=[dict(problem="good_trade", targets=["R99-death"], analysis="值得复盘。")])
    d = diagnose(match, mm, CFG, BASELINE, claude([submit_response(bad)] * 3)[0])
    assert not d.submission.patterns and not d.submission.highlights
    assert [item.path for item in d.dropped] == ["patterns[0]", "highlights[0]"]


def test_excess_other_entries_are_pruned_and_rechecked(match, mm):
    bad = payload(findings=[copy.deepcopy(payload()["findings"][0]) for _ in range(4)])
    d = diagnose(match, mm, CFG, BASELINE, claude([submit_response(bad)] * 3)[0])
    assert len(d.submission.findings) == 1
    assert [item.path for item in d.dropped] == ["findings[1]", "findings[2]", "findings[3]"]
    assert all("duplicate_problem" in {v.code for v in item.violations} for item in d.dropped)


def test_zero_repairs_prunes_immediately(match, mm):
    cfg = copy.deepcopy(CFG)
    cfg["ai"]["max_repairs"] = 0
    bad = payload(overview="987654321回合。")
    d = diagnose(match, mm, cfg, BASELINE, claude([submit_response(bad)], cfg)[0])
    assert d.repairs == 0 and d.dropped[0].action == "replaced"


def test_multiple_submissions_in_one_turn_are_rejected(match, mm):
    duplicate = claude_resp([tool_block("submit_report", payload(), "a"), tool_block("submit_report", payload(), "b")], "tool_use")
    d = diagnose(match, mm, CFG, BASELINE, claude([duplicate, submit_response()])[0])
    assert d.repairs == 1 and d.violations_history[0][0].code == "multiple_submissions"


def test_claude_refusal_truncation_and_fallback_setting(match, mm):
    refusal = claude_resp([], "refusal")
    refusal.stop_details = SimpleNamespace(category="cyber")
    with pytest.raises(DiagnosisError, match="拒绝"):
        diagnose(match, mm, CFG, BASELINE, claude([refusal])[0])
    with pytest.raises(DiagnosisError, match="max_tokens"):
        diagnose(match, mm, CFG, BASELINE, claude([claude_resp([], "max_tokens")])[0])
    cfg = copy.deepcopy(CFG)
    cfg["ai"]["claude"]["server_fallback"] = False
    provider, fake = claude([submit_response()], cfg)
    diagnose(match, mm, cfg, BASELINE, provider)
    assert "fallbacks" not in fake.calls[0] and "betas" not in fake.calls[0]


def oa_resp(content, finish, tool_calls=None, reasoning="（思考过程）"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], model="deepseek-v4-pro",
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40, prompt_cache_hit_tokens=60))


def oa_call(name, arguments, id_="c1"):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=arguments))


class FakeOpenAI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


def test_deepseek_diagnose_preserves_reasoning_and_tool_results(match, mm):
    fake = FakeOpenAI([oa_resp("", "tool_calls", [oa_call("get_round", '{"round": 1}')], reasoning="先看第一回合"),
                       oa_resp("", "tool_calls", [oa_call("submit_report", json.dumps(payload()), "c2")])])
    d = diagnose(match, mm, CFG, BASELINE, OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))
    assert d.provider == "deepseek" and d.repairs == 0
    assert d.usage.input_tokens == 200 and d.usage.cache_read_tokens == 120
    first, second = fake.calls
    assert first["extra_body"] == {"thinking": {"type": "enabled"}} and first["reasoning_effort"] == "high"
    assert {t["function"]["name"] for t in first["tools"]} == {"get_moment", "get_round", "submit_report"}
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    asst, tool = second["messages"][2], second["messages"][3]
    assert asst["reasoning_content"] == "先看第一回合" and asst["tool_calls"][0]["id"] == "c1"
    assert tool["role"] == "tool" and json.loads(tool["content"])["round"] == 1


@pytest.mark.parametrize("name", ["get_moment", "submit_report"])
def test_deepseek_malformed_json_arguments(name, match, mm):
    fake = FakeOpenAI([oa_resp("", "tool_calls", [oa_call(name, "{invalid")]),
                       oa_resp("", "tool_calls", [oa_call("submit_report", json.dumps(payload()), "c2")])])
    d = diagnose(match, mm, CFG, BASELINE, OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))
    assert d.tool_calls[0]["error"] and d.repairs == int(name == "submit_report")
    assert fake.calls[1]["messages"][3]["content"].startswith("错误：")
    if name == "submit_report":
        assert d.raw_calls[0] == {"raw_args": "{invalid", "parsed_args": None}


def test_deepseek_empty_object_preserves_raw_and_parsed_arguments(match, mm):
    fake = FakeOpenAI([oa_resp("", "tool_calls", [oa_call("submit_report", "{}")]),
                       oa_resp("", "tool_calls", [oa_call("submit_report", json.dumps(payload()), "c2")])])
    d = diagnose(match, mm, CFG, BASELINE, OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))
    assert d.repairs == 0 and d.raw_calls[0] == {"raw_args": "{}", "parsed_args": {}}
    assert "提交内容为空，请提交完整对象" in fake.calls[1]["messages"][3]["content"]


def test_deepseek_single_wrapper_uses_inner_report_and_keeps_wire_arguments(match, mm):
    raw = json.dumps({"report": payload()}, ensure_ascii=False)
    fake = FakeOpenAI([oa_resp("", "tool_calls", [oa_call("submit_report", raw)])])
    d = diagnose(match, mm, CFG, BASELINE, OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))
    assert d.repairs == 0 and d.submission.model_dump() == payload()
    assert d.raw_calls == [{"raw_args": raw, "parsed_args": {"report": payload()}, "unwrapped": "report"}]


@pytest.mark.parametrize("finish,message", [("length", "max_tokens"), ("content_filter", "拒绝"), ("insufficient_system_resource", "异常")])
def test_deepseek_stop_reasons(finish, message, match, mm):
    fake = FakeOpenAI([oa_resp("…", finish)])
    with pytest.raises(DiagnosisError, match=message):
        diagnose(match, mm, CFG, BASELINE, OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))


def test_provider_configuration_errors(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        make_provider(CFG)
    cfg = copy.deepcopy(CFG)
    cfg["ai"]["provider"] = "gpt"
    with pytest.raises(ProviderError):
        make_provider(cfg)


def test_max_turns_still_limits_query_loop(match, mm):
    cfg = copy.deepcopy(CFG)
    cfg["ai"]["max_turns"] = 1
    with pytest.raises(DiagnosisError, match="超过 1 轮"):
        diagnose(match, mm, cfg, BASELINE, claude([claude_resp([tool_block("get_round", {"round": 1})], "tool_use")])[0])


def test_summary_section_lists_ids_by_pattern(mm):
    from cs2coach.tools.digest import summary_section
    text = summary_section(mm)
    assert "击杀 1 次" in text and "死亡 1 次" in text
    assert "死时身上还有没用的道具：1/1 次：[[R1-death]]" in text
    assert "没朝击杀者开枪就阵亡（不含武器还不能开枪的）：1/1 次：[[R1-death]]" in text
    assert "被闪致死：1/1 次：[[R1-death]]" in text and "首杀：1/1 次：[[R1-kill1]]" in text


def test_summary_excludes_post_round_and_world_deaths(match):
    from tests.test_moments import death, hurt
    from cs2coach.tools.digest import summary_section
    m = scenario(kills=[death(100, A, C), death(110, None, A, weapon="planted_c4")],
                 damages=[hurt(100, A, C, 100, 0), hurt(110, None, A, 100, 0, weapon="planted_c4")])
    text = summary_section(find_moments(m, A, CFG))
    assert "死亡 1 次，其中交火中阵亡 0 次" in text and "[[R1-death]] 是回合结束后或非玩家伤害致死" in text
