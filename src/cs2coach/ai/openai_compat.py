"""OpenAI 兼容接口（DeepSeek 与本机 Ollama）。

DeepSeek 的要点（https://api-docs.deepseek.com，2026-10-15 查阅）：
- base_url https://api.deepseek.com，用 openai SDK；凭据环境变量 DEEPSEEK_API_KEY
- 思考模式默认开启，通过 extra_body={"thinking": {"type": "enabled"}} 控制；reasoning_effort 取 low / high / max
- 带工具的对话中，之前每一轮助手消息的 reasoning_content 都必须原样带回，否则返回 400
- 思考模式下 tool_choice 只能是 auto；工具参数 function.arguments 可能不是合法 JSON，要先校验
- 上下文缓存自动生效（前缀匹配），对话只追加不修改即可命中
"""

from __future__ import annotations

import json
import math
import os
import re
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from typing import Any

from cs2coach.ai.providers import ProviderError, ToolCall, ToolResult, Turn, Usage

_FINISH = {"stop": "end", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


def _ollama_num_ctx(base_url: str, model: str) -> int:
    """从 Ollama 原生 /api/show 的 parameters 中读取模型实际上下文。"""
    parts = urlsplit(base_url)
    url = urlunsplit((parts.scheme, parts.netloc, "/api/show", "", ""))
    request = Request(url, data=json.dumps({"model": model}).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            data = json.load(response)
    except (URLError, OSError, ValueError) as exc:
        raise ProviderError(f"无法查询 Ollama 模型 {model} 的上下文长度：{exc}") from exc
    parameters = data.get("parameters", "") if isinstance(data, dict) else ""
    match = re.search(r"(?:^|\n)\s*num_ctx\s+(\d+)\b", str(parameters))
    if match is None or int(match[1]) <= 0:
        raise ProviderError(f"Ollama 模型 {model} 未声明 num_ctx；请用包含 PARAMETER num_ctx 的 Modelfile 新建大上下文模型。")
    return int(match[1])


class OpenAICompatProvider:
    def __init__(self, name: str, cfg: dict[str, Any], client: Any = None):
        self.name = name
        self.model = cfg["model"]
        self.cfg = cfg
        self.reasoning_field = cfg.get("reasoning_field", "reasoning_content")
        self.num_ctx: int | None = None
        self.prompt_tokens_history: list[int] = []
        if client is None:
            import openai

            key = os.environ.get(cfg["api_key_env"]) if cfg["api_key_env"] else "local-placeholder"
            if not key:
                raise ProviderError(f"没有找到 {name} 的 API 凭据：请设置环境变量 {cfg['api_key_env']}")
            client = openai.OpenAI(api_key=key, base_url=cfg["base_url"])
        self.client = client
        self.params: dict[str, Any] = {"model": cfg["model"], "max_tokens": int(cfg["max_tokens"])}
        if "thinking" in cfg:
            if cfg["thinking"]:
                self.params["reasoning_effort"] = cfg["reasoning_effort"]
                self.params["extra_body"] = {"thinking": {"type": "enabled"}}
            else:
                self.params["extra_body"] = {"thinking": {"type": "disabled"}}
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, user_texts: list[str], tools: list[dict[str, Any]]) -> None:
        self.prompt_tokens_history = []
        if self.cfg.get("context_check") == "ollama":
            self.num_ctx = _ollama_num_ctx(self.cfg["base_url"], self.model)
            size = len(system) + len("\n\n".join(user_texts)) + len(json.dumps(tools, ensure_ascii=False))
            estimated = math.ceil(size * self.cfg["chars_per_token"])
            required = estimated + self.params["max_tokens"] + self.cfg["context_margin_tokens"]
            if required > self.num_ctx:
                raise ProviderError(f"预计首轮输入约 {estimated} tokens，输出上限 {self.params['max_tokens']}、"
                                    f"余量 {self.cfg['context_margin_tokens']}，合计 {required} 超过模型上下文 {self.num_ctx}；"
                                    "请改用 num_ctx 更大的 Ollama 模型。")
        else:
            self.num_ctx = None
        self.params["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": "\n\n".join(user_texts)}]

    def send(self) -> Turn:
        resp = self.client.chat.completions.create(messages=self.messages, **self.params)
        u = getattr(resp, "usage", None)
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        if self.num_ctx is not None:
            if prompt_tokens <= 0:
                raise ProviderError("Ollama 未返回 prompt_tokens，无法确认上下文是否被截断。")
            if self.prompt_tokens_history and prompt_tokens < self.prompt_tokens_history[-1]:
                raise ProviderError(f"Ollama 本轮 prompt_tokens 从 {self.prompt_tokens_history[-1]} 降为 "
                                    f"{prompt_tokens}，历史可能已被截断；请改用更大上下文模型。")
            limit = self.num_ctx - self.params["max_tokens"]
            if prompt_tokens > limit:
                raise ProviderError(f"Ollama 本轮 prompt_tokens 为 {prompt_tokens}，超过安全上限 {limit} "
                                    f"（num_ctx {self.num_ctx} 减输出上限 {self.params['max_tokens']}）；"
                                    "请改用更大上下文模型。")
        self.prompt_tokens_history.append(prompt_tokens)
        usage = Usage(
            input_tokens=prompt_tokens,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "prompt_cache_hit_tokens", 0) or 0,
        )
        model = getattr(resp, "model", None) or self.model
        choice = resp.choices[0]
        msg = choice.message
        stop = _FINISH.get(choice.finish_reason, "error")
        if stop in ("max_tokens", "refusal", "error"):
            return Turn(stop=stop, usage=usage, model=model, detail=choice.finish_reason)

        tool_calls = list(getattr(msg, "tool_calls", None) or [])
        # DeepSeek 必须回传 reasoning_content；Ollama 回传 reasoning 供多轮使用。
        entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        reasoning = getattr(msg, self.reasoning_field, None)
        if reasoning is None and getattr(msg, "model_extra", None):
            reasoning = msg.model_extra.get(self.reasoning_field)
        if reasoning is not None:
            entry[self.reasoning_field] = reasoning
        if tool_calls:
            entry["tool_calls"] = [{"id": c.id, "type": "function",
                                    "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                   for c in tool_calls]
        self.messages.append(entry)

        calls = [ToolCall(id=c.id, name=c.function.name, args=_parse_args(c.function.arguments),
                          raw_args=c.function.arguments or "") for c in tool_calls]
        return Turn(stop="tool_use" if calls else "end", text=(msg.content or "").strip(), tool_calls=calls,
                    usage=usage, model=model)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            content = f"错误：{r.content}" if r.is_error else r.content
            self.messages.append({"role": "tool", "tool_call_id": r.id, "content": content})

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def limit_to_submission(self) -> None:
        """查询额度耗尽后，后续请求只向模型提供提交工具。"""
        self.params["tools"] = [tool for tool in self.params["tools"]
                                if tool["function"]["name"] == "submit_report"]


def _parse_args(raw: str | None) -> dict[str, Any] | None:
    try:
        v = json.loads(raw or "")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None
