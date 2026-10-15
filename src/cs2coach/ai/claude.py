"""Claude（Anthropic 官方 SDK）。

- 自适应思考 + effort；工具 strict 模式
- 自动提示词缓存：多轮工具调用时系统提示词、工具定义和摘要都从缓存读取
- 可选服务端拒绝回退（server-side-fallback，按拒绝类别换推荐的备用模型）
- 每轮把模型返回的完整 content（含 thinking 块）原样追加，不修改历史
"""

from __future__ import annotations

import json
from typing import Any

from cs2coach.ai.providers import ToolCall, ToolResult, Turn, Usage

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeProvider:
    name = "claude"

    def __init__(self, cfg: dict[str, Any], client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()  # 读取 ANTHROPIC_API_KEY 或 `ant auth login` 的凭据
        self.client = client
        self.model = cfg["model"]
        self.params: dict[str, Any] = {
            "model": cfg["model"],
            "max_tokens": int(cfg["max_tokens"]),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": cfg["effort"]},
            "cache_control": {"type": "ephemeral"},
        }
        if cfg.get("server_fallback"):
            self.params["betas"] = [_FALLBACK_BETA]
            self.params["fallbacks"] = "default"
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, user_texts: list[str], tools: list[dict[str, Any]]) -> None:
        self.params["system"] = system
        self.params["tools"] = [{**t, "strict": True} for t in tools]
        self.messages = [{"role": "user", "content": [{"type": "text", "text": t} for t in user_texts]}]

    def send(self) -> Turn:
        resp = self.client.beta.messages.create(messages=self.messages, **self.params)
        u = getattr(resp, "usage", None)
        usage = Usage(
            input_tokens=sum(getattr(u, f, 0) or 0 for f in
                             ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        model = getattr(resp, "model", None) or self.model
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            return Turn(stop="refusal", usage=usage, model=model, detail=getattr(details, "category", None))
        if resp.stop_reason == "max_tokens":
            return Turn(stop="max_tokens", usage=usage, model=model)
        self.messages.append({"role": "assistant", "content": resp.content})
        calls = []
        for b in resp.content:
            if b.type == "tool_use":
                args = b.input if isinstance(b.input, dict) else _parse_args(b.input)
                calls.append(ToolCall(id=b.id, name=b.name, args=args, raw_args=json.dumps(b.input, ensure_ascii=False)))
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return Turn(stop="tool_use" if calls else "end", text=text, tool_calls=calls, usage=usage, model=model)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # 同一轮的所有结果放在一条用户消息里
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.id, "content": r.content, "is_error": r.is_error} for r in results]})

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


def _parse_args(raw: Any) -> dict[str, Any] | None:
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None
