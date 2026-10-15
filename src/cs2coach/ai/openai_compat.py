"""OpenAI 兼容接口（DeepSeek；以后的通义千问等同一套）。

DeepSeek 的要点（https://api-docs.deepseek.com，2026-10-15 查阅）：
- base_url https://api.deepseek.com，用 openai SDK；凭据环境变量 DEEPSEEK_API_KEY
- 思考模式默认开启，通过 extra_body={"thinking": {"type": "enabled"}} 控制；reasoning_effort 取 low / high / max
- 带工具的对话中，之前每一轮助手消息的 reasoning_content 都必须原样带回，否则返回 400
- 思考模式下 tool_choice 只能是 auto；工具参数 function.arguments 可能不是合法 JSON，要先校验
- 上下文缓存自动生效（前缀匹配），对话只追加不修改即可命中
"""

from __future__ import annotations

import json
import os
from typing import Any

from cs2coach.ai.providers import ProviderError, ToolCall, ToolResult, Turn, Usage

_FINISH = {"stop": "end", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


class OpenAICompatProvider:
    def __init__(self, name: str, cfg: dict[str, Any], client: Any = None):
        self.name = name
        self.model = cfg["model"]
        if client is None:
            import openai

            key = os.environ.get(cfg["api_key_env"])
            if not key:
                raise ProviderError(f"没有找到 {name} 的 API 凭据：请设置环境变量 {cfg['api_key_env']}")
            client = openai.OpenAI(api_key=key, base_url=cfg["base_url"])
        self.client = client
        self.params: dict[str, Any] = {"model": cfg["model"], "max_tokens": int(cfg["max_tokens"])}
        if cfg.get("thinking", True):
            self.params["reasoning_effort"] = cfg["reasoning_effort"]
            self.params["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            self.params["extra_body"] = {"thinking": {"type": "disabled"}}
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, user_texts: list[str], tools: list[dict[str, Any]]) -> None:
        self.params["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": "\n\n".join(user_texts)}]

    def send(self) -> Turn:
        resp = self.client.chat.completions.create(messages=self.messages, **self.params)
        u = getattr(resp, "usage", None)
        usage = Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
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
        # 原样带回 reasoning_content（DeepSeek 带工具的对话必须如此）
        entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning is None and getattr(msg, "model_extra", None):
            reasoning = msg.model_extra.get("reasoning_content")
        if reasoning is not None:
            entry["reasoning_content"] = reasoning
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


def _parse_args(raw: str | None) -> dict[str, Any] | None:
    try:
        v = json.loads(raw or "")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None
