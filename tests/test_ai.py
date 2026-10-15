"""职业基准、整场摘要、查询工具、引用检查、AI 诊断循环（假客户端，不调用真实 API）。"""

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
from cs2coach.tools.citations import check_citations, render_report
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


# ---------- 职业基准 ----------


def test_compare_respects_metric_direction():
    # Time to Damage 越小越好
    c = compare("time_to_damage_ms", 360, BASELINE)
    assert c["pro_median"] == 375 and c["pro_n"] == 4 and c["better_than_pro_pct"] == 50
    assert compare("time_to_damage_ms", 250, BASELINE)["better_than_pro_pct"] == 100
    # Counter-Strafing 越大越好
    assert compare("counter_strafing_pct", 85, BASELINE)["better_than_pro_pct"] == 67
    assert compare("counter_strafing_pct", None, BASELINE) is None
    assert compare("spray_accuracy_pct", 30, BASELINE) is None  # 基准里没有这一项


# ---------- 摘要 ----------


def test_digest_lists_every_moment_with_citation_ids(match, mm):
    text = build_digest(match, mm, BASELINE)
    for m in mm.moments:
        assert f"[[{m.id}]]" in text
    assert "## 比赛" in text and "## 整场枪法" in text and "测试样本" in text
    assert "R1T" in text  # 每回合的阵营与胜负
    assert "[[R1]]" in text  # 漏补按回合引用
    assert "道具：烟" in text  # 死亡时没用的烟


# ---------- 查询工具 ----------


def test_tool_schemas_are_strict_compatible():
    for t in TOOLS:
        s = t["input_schema"]
        assert s["additionalProperties"] is False and s["required"]


def test_get_moment(mm):
    data = json.loads(get_moment("R1-death", mm))
    assert data["id"] == "R1-death" and data["opponent"] == D
    with pytest.raises(ToolError, match="R1-kill1"):  # 编号不存在时列出可用编号
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


# ---------- 引用 ----------


def test_check_citations(mm):
    ok = check_citations("见 [[R1-kill1]]，以及 [[R1]] 和 [[ R1-death ]]。", mm)
    assert ok.ok and ok.cited == ["R1-kill1", "R1", "R1-death"]
    bad = check_citations("见 [[R1-kill9]] 和 [[R7]]", mm)
    assert bad.invalid == ["R1-kill9", "R7"] and not bad.ok
    assert not check_citations("没有引用", mm).ok


def test_render_report_appends_jump_commands(mm):
    out = render_report("### [[R1-death]] 被闪后硬拉\n参考 [[R1]]", mm)
    assert "〔R1-death〕" in out and "[[" not in out
    death = next(m for m in mm.moments if m.id == "R1-death")
    assert f"`{death.jump_command}`" in out and "| R1-death | 1 |" in out


# ---------- 诊断循环（Claude，假客户端） ----------


def text_block(t):
    return SimpleNamespace(type="text", text=t)


def tool_block(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def claude_resp(blocks, stop):
    return SimpleNamespace(content=blocks, stop_reason=stop, model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=20, output_tokens=50,
                                                 cache_read_input_tokens=80, cache_creation_input_tokens=0))


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


def test_claude_diagnose_with_tool_call(match, mm):
    provider, fake = claude([
        claude_resp([tool_block("get_moment", {"moment_id": "R1-death"})], "tool_use"),
        claude_resp([text_block("# 复盘\n### [[R1-death]] 没开枪就被打死")], "end_turn"),
    ])
    d = diagnose(match, mm, CFG, BASELINE, provider=provider)
    assert d.citations.ok and "〔R1-death〕" in d.report and "demo_gototick" in d.report
    assert d.provider == "claude" and d.tool_calls == [{"name": "get_moment", "input": {"moment_id": "R1-death"}, "error": False}]
    assert d.usage.input_tokens == 200 and d.usage.cache_read_tokens == 160

    first, second = fake.calls
    assert first["model"] == CFG["ai"]["claude"]["model"] and first["thinking"] == {"type": "adaptive"}
    assert first["cache_control"] == {"type": "ephemeral"} and first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"])
    assert "[[R1-death]]" in first["messages"][0]["content"][0]["text"]  # 摘要在第一条用户消息里
    # 只追加：助手的完整 content 原样放回，工具结果在下一条用户消息里
    assert second["messages"][1]["role"] == "assistant"
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1" and not result["is_error"]
    assert json.loads(result["content"])["id"] == "R1-death"


def test_tool_error_is_reported_to_model(match, mm):
    provider, fake = claude([
        claude_resp([tool_block("get_moment", {"moment_id": "R9-kill1"})], "tool_use"),
        claude_resp([text_block("[[R1-kill1]]")], "end_turn"),
    ])
    d = diagnose(match, mm, CFG, BASELINE, provider=provider)
    assert d.tool_calls[0]["error"] is True
    assert fake.calls[1]["messages"][2]["content"][0]["is_error"] is True


def test_repairs_invalid_citations(match, mm):
    provider, fake = claude([
        claude_resp([text_block("[[R3-kill1]] 不存在")], "end_turn"),
        claude_resp([text_block("改正后：[[R1-kill1]]")], "end_turn"),
    ])
    d = diagnose(match, mm, CFG, BASELINE, provider=provider)
    assert d.repairs == 1 and d.citations.ok and "改正后" in d.report
    assert "R3-kill1" in fake.calls[1]["messages"][-1]["content"]


def test_gives_up_after_max_repairs(match, mm):
    provider, _ = claude([claude_resp([text_block("[[R3-kill1]]")], "end_turn")] * 2)
    d = diagnose(match, mm, CFG, BASELINE, provider=provider)
    assert d.repairs == 1 and d.citations.invalid == ["R3-kill1"]


def test_refusal_and_truncation(match, mm):
    refusal = claude_resp([], "refusal")
    refusal.stop_details = SimpleNamespace(category="cyber")
    with pytest.raises(DiagnosisError, match="拒绝"):
        diagnose(match, mm, CFG, BASELINE, provider=claude([refusal])[0])
    with pytest.raises(DiagnosisError, match="max_tokens"):
        diagnose(match, mm, CFG, BASELINE, provider=claude([claude_resp([text_block("…")], "max_tokens")])[0])


def test_claude_without_server_fallback(match, mm):
    cfg = copy.deepcopy(CFG)
    cfg["ai"]["claude"]["server_fallback"] = False
    provider, fake = claude([claude_resp([text_block("[[R1]]")], "end_turn")], cfg)
    diagnose(match, mm, cfg, BASELINE, provider=provider)
    assert "fallbacks" not in fake.calls[0] and "betas" not in fake.calls[0]


# ---------- 诊断循环（DeepSeek / OpenAI 兼容接口，假客户端） ----------


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


def test_deepseek_diagnose_passes_reasoning_back(match, mm):
    fake = FakeOpenAI([
        oa_resp("", "tool_calls", [oa_call("get_round", '{"round": 1}')], reasoning="先看第一回合"),
        oa_resp("报告 [[R1-kill1]]", "stop"),
    ])
    provider = OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake)
    d = diagnose(match, mm, CFG, BASELINE, provider=provider)
    assert d.provider == "deepseek" and d.citations.ok and "〔R1-kill1〕" in d.report
    assert d.usage.input_tokens == 200 and d.usage.cache_read_tokens == 120

    first, second = fake.calls
    assert first["model"] == CFG["ai"]["deepseek"]["model"]
    assert first["extra_body"] == {"thinking": {"type": "enabled"}} and first["reasoning_effort"] == "high"
    assert first["tools"][0]["type"] == "function" and first["tools"][0]["function"]["name"] == "get_moment"
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    assert "[[R1-kill1]]" in first["messages"][1]["content"]
    # 带工具的对话：助手消息必须带回 reasoning_content，工具结果用 role=tool
    asst, tool = second["messages"][2], second["messages"][3]
    assert asst["reasoning_content"] == "先看第一回合" and asst["tool_calls"][0]["id"] == "c1"
    assert tool["role"] == "tool" and tool["tool_call_id"] == "c1" and json.loads(tool["content"])["round"] == 1


def test_deepseek_invalid_tool_arguments(match, mm):
    fake = FakeOpenAI([
        oa_resp("", "tool_calls", [oa_call("get_moment", "{moment_id: R1")]),
        oa_resp("[[R1]]", "stop"),
    ])
    d = diagnose(match, mm, CFG, BASELINE, provider=OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))
    assert d.tool_calls[0]["error"] is True
    assert fake.calls[1]["messages"][3]["content"].startswith("错误：")


def test_deepseek_stop_reasons(match, mm):
    for finish, msg in (("length", "max_tokens"), ("content_filter", "拒绝"), ("insufficient_system_resource", "异常")):
        fake = FakeOpenAI([oa_resp("…", finish)])
        with pytest.raises(DiagnosisError, match=msg):
            diagnose(match, mm, CFG, BASELINE, provider=OpenAICompatProvider("deepseek", CFG["ai"]["deepseek"], fake))


def test_deepseek_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        make_provider(CFG)  # 默认 provider 是 deepseek


def test_unknown_provider():
    cfg = copy.deepcopy(CFG)
    cfg["ai"]["provider"] = "gpt"
    with pytest.raises(ProviderError):
        make_provider(cfg)


# ---------- 整场汇总、裸写编号 ----------


def test_summary_section_lists_ids_by_pattern(mm):
    from cs2coach.tools.digest import summary_section

    text = summary_section(mm)
    assert "击杀 1 次" in text and "死亡 1 次" in text
    assert "死时身上还有没用的道具：1/1 次：[[R1-death]]" in text
    assert "没朝击杀者开枪就阵亡（不含武器还不能开枪的）：1/1 次：[[R1-death]]" in text
    assert "被闪致死：1/1 次：[[R1-death]]" in text
    assert "首杀：1/1 次：[[R1-kill1]]" in text
    assert "死于 AWP：0/1 次：无" in text


def test_bare_references_are_flagged(mm):
    c = check_citations("[[R1-death]] 和 R1、R1-kill1 一样；R8 左轮、R8 Revolver 不算", mm)
    assert c.bare == ["R1", "R1-kill1"] and not c.ok


def test_repair_message_covers_invalid_and_bare_together(match, mm):
    provider, fake = claude([
        claude_resp([text_block("[[R3-kill1]] 和 R1")], "end_turn"),
        claude_resp([text_block("[[R1-kill1]] 和 [[R1]]")], "end_turn"),
    ])
    d = diagnose(match, mm, CFG, BASELINE, provider=provider)
    msg = fake.calls[1]["messages"][-1]["content"]
    assert "R3-kill1" in msg and "R1" in msg and msg.count("请改正后重新输出完整的报告") == 1
    assert d.repairs == 1 and d.citations.ok


def test_summary_excludes_post_round_and_world_deaths(match):
    # A 在回合结束后（tick 110 > end_tick 100）被炸弹炸死：不算交火中的阵亡
    from tests.test_moments import death, hurt
    from cs2coach.tools.digest import summary_section

    m = scenario(kills=[death(100, A, C), death(110, None, A, weapon="planted_c4")],
                 damages=[hurt(100, A, C, 100, 0), hurt(110, None, A, 100, 0, weapon="planted_c4")])
    text = summary_section(find_moments(m, A, CFG))
    assert "死亡 1 次，其中交火中阵亡 0 次" in text and "[[R1-death]] 是回合结束后或非玩家伤害致死" in text
    assert "人数优势时阵亡：0/0 次：无" in text
