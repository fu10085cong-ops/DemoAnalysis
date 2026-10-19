"""本地 Ollama 适配与上下文保护；HTTP 和模型回复均用假对象。"""

import copy
import io
import json
from types import SimpleNamespace

import pytest

from cs2coach import cli
from cs2coach.ai.diagnose import diagnose
from cs2coach.ai.openai_compat import OpenAICompatProvider
from cs2coach.ai.providers import ProviderError, make_provider
from cs2coach.baseline import build_baseline
from cs2coach.config import load_config
from tests.helpers.synthetic import A
from tests.test_ai import FakeOpenAI, oa_call, payload
from tests.test_moments import find_moments, scenario
from tests.test_summary_ai import sample


def response(prompt, call=None, reasoning="本地思考"):
    msg = SimpleNamespace(content="", tool_calls=[call] if call else [],
                          model_extra={"reasoning": reasoning})
    return SimpleNamespace(choices=[SimpleNamespace(message=msg,
        finish_reason="tool_calls" if call else "stop")], model="qwen3-14b-40k",
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=20))


def local(monkeypatch, responses, num_ctx=40960):
    seen = []
    def show(request, timeout):
        seen.append((request.full_url, json.loads(request.data)))
        return io.BytesIO(json.dumps({"parameters": f"num_ctx {num_ctx}\n"}).encode())
    monkeypatch.setattr("cs2coach.ai.openai_compat.urlopen", show)
    cfg = load_config()["ai"]["local"]
    fake = FakeOpenAI(responses)
    return OpenAICompatProvider("local", cfg, fake), fake, seen


def test_local_omits_deepseek_arguments_and_replays_reasoning(monkeypatch):
    provider, fake, seen = local(monkeypatch, [response(100, oa_call("get_moment", '{"moment_id":"R1-death"}')),
                                         response(200)])
    provider.start("system", ["摘要"], [{"name": "get_moment", "description": "查询", "input_schema": {}},
                                      {"name": "submit_report", "description": "提交", "input_schema": {}}])
    provider.send()
    provider.send()
    assert seen == [("http://localhost:11434/api/show", {"model": "qwen3-14b-40k"})]
    assert "extra_body" not in fake.calls[0] and "reasoning_effort" not in fake.calls[0]
    assert fake.calls[1]["messages"][2]["reasoning"] == "本地思考"
    assert provider.prompt_tokens_history == [100, 200]


def test_ollama_context_preflight_fails_before_model_call(monkeypatch):
    provider, fake, _ = local(monkeypatch, [], num_ctx=4096)
    with pytest.raises(ProviderError, match="预计首轮输入.*超过模型上下文 4096"):
        provider.start("x" * 3000, ["摘要"], [])
    assert fake.calls == []


def test_ollama_missing_num_ctx_is_error(monkeypatch):
    monkeypatch.setattr("cs2coach.ai.openai_compat.urlopen",
                        lambda request, timeout: io.BytesIO(b'{"parameters":"temperature 0.7"}'))
    cfg = load_config()["ai"]["local"]
    provider = OpenAICompatProvider("local", cfg, FakeOpenAI([]))
    with pytest.raises(ProviderError, match="未声明 num_ctx"):
        provider.start("系统", ["摘要"], [])


def test_ollama_post_turn_context_limit_and_decrease(monkeypatch):
    provider, _, _ = local(monkeypatch, [response(33000)])
    provider.start("系统", ["摘要"], [])
    with pytest.raises(ProviderError, match="超过安全上限"):
        provider.send()
    provider, _, _ = local(monkeypatch, [response(100), response(90)])
    provider.start("系统", ["摘要"], [])
    provider.send()
    with pytest.raises(ProviderError, match="历史可能已被截断"):
        provider.send()


def test_local_query_cap_only_offers_submit_report(monkeypatch):
    cfg = load_config()
    cfg["ai"]["local"]["max_tool_calls"] = 1
    fake = FakeOpenAI([response(100, oa_call("get_round", '{"round":1}')),
                       response(200, oa_call("submit_report", json.dumps(payload()), "c2"))])
    monkeypatch.setattr("cs2coach.ai.openai_compat._ollama_num_ctx", lambda *_: 40960)
    provider = OpenAICompatProvider("local", cfg["ai"]["local"], fake)
    match = scenario()
    mm = find_moments(match, A, cfg)
    baseline = build_baseline({"time_to_damage_ms": [300, 350]}, "测试")
    result = diagnose(match, mm, cfg, baseline, provider)
    assert result.prompt_tokens_history == [100, 200]
    assert {tool["function"]["name"] for tool in fake.calls[0]["tools"]} == {
        "get_moment", "get_round", "submit_report"}
    assert [tool["function"]["name"] for tool in fake.calls[1]["tools"]] == ["submit_report"]
    assert "查询次数已用完" in fake.calls[1]["messages"][-1]["content"]


def test_provider_override_for_both_commands_and_local_factory(monkeypatch, tmp_path):
    cfg = load_config()
    cfg["ai"]["provider"] = "local"
    provider = make_provider(cfg, FakeOpenAI([]))
    assert provider.name == "local" and provider.params["max_tokens"] == 8192
    assert "extra_body" not in provider.params
    assert cli._build_parser().parse_args(["analyze", "x.dem", "--player", str(A),
                                           "--provider", "local"]).provider == "local"
    summary, _, _ = sample()
    seen = {}
    def build(sid, refs, config, baseline, jobs=None):
        seen["provider"] = config["ai"]["provider"]
        return summary
    monkeypatch.setattr(cli, "load_config", lambda path=None: copy.deepcopy(load_config()))
    monkeypatch.setattr("cs2coach.summary.discover.find_player_demos", lambda *_: ["demo"])
    monkeypatch.setattr("cs2coach.summary.aggregate.build_summary", build)
    monkeypatch.setattr("cs2coach.baseline.load_baseline", lambda: None)
    monkeypatch.setattr(cli, "_summary_ai", lambda *args: 0)
    assert cli.main(["summary", "--player", str(A), "--provider", "local",
                     "--ai", "--out", str(tmp_path)]) == 0
    assert seen["provider"] == "local"


def test_model_flag_overrides_selected_provider_model(monkeypatch, tmp_path):
    summary, _, _ = sample()
    seen = {}
    def build(sid, refs, config, baseline, jobs=None):
        seen["ai"] = copy.deepcopy(config["ai"])
        return summary
    monkeypatch.setattr(cli, "load_config", lambda path=None: copy.deepcopy(load_config()))
    monkeypatch.setattr("cs2coach.summary.discover.find_player_demos", lambda *_: ["demo"])
    monkeypatch.setattr("cs2coach.summary.aggregate.build_summary", build)
    monkeypatch.setattr("cs2coach.baseline.load_baseline", lambda: None)
    monkeypatch.setattr(cli, "_summary_ai", lambda *args: 0)
    assert cli.main(["summary", "--player", str(A), "--provider", "deepseek", "--model", "deepseek-v4-pro",
                     "--ai", "--out", str(tmp_path)]) == 0
    assert seen["ai"]["provider"] == "deepseek" and seen["ai"]["deepseek"]["model"] == "deepseek-v4-pro"
    assert seen["ai"]["local"]["model"] == load_config()["ai"]["local"]["model"]  # 其他服务不受影响
    assert cli._build_parser().parse_args(["analyze", "x.dem", "--player", str(A),
                                           "--model", "deepseek-flash"]).model == "deepseek-flash"


def test_mimo_factory_and_missing_key(monkeypatch):
    """038：小米 MiMo 走 OpenAI 兼容接口，不发 DeepSeek 的 thinking 参数；缺凭据时报出环境变量名。"""
    cfg = load_config()
    cfg["ai"]["provider"] = "mimo"
    provider = make_provider(cfg, FakeOpenAI([]))
    assert provider.name == "mimo" and provider.params["model"] == "mimo-v2.6-flash"
    assert "extra_body" not in provider.params
    assert cli._build_parser().parse_args(["summary", "--player", str(A), "--provider", "mimo"]).provider == "mimo"
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="MIMO_API_KEY"):
        make_provider(cfg)
