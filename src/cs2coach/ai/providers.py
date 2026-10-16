"""模型服务的统一接口：诊断循环只和这里的类型打交道，不关心具体是哪家的模型（CLAUDE.md 架构原则 4）。

每个服务自己保存对话历史（各家的消息格式不同，例如 Claude 的 thinking 块、DeepSeek 的 reasoning_content
都必须原样带回），对外只提供"追加"操作：对话只追加不修改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StopKind = Literal["end", "tool_use", "max_tokens", "refusal", "error"]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] | None  # 参数不是合法 JSON 对象时为 None
    raw_args: str = ""


@dataclass
class ToolResult:
    id: str
    content: str
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0  # 含缓存命中
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class Turn:
    """模型的一次回复。"""

    stop: StopKind
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    detail: str | None = None  # 拒绝类别、错误原因等


class ChatProvider(Protocol):
    name: str
    model: str

    def start(self, system: str, user_texts: list[str], tools: list[dict[str, Any]]) -> None:
        """开始新对话：系统提示词、第一条用户消息（可分多段）、工具定义（JSON Schema，见 tools/queries.py）。"""

    def send(self) -> Turn:
        """按当前历史请求一次，并把模型的回复追加到历史中。"""

    def add_tool_results(self, results: list[ToolResult]) -> None: ...

    def add_user(self, text: str) -> None: ...


class ProviderError(Exception):
    """配置或凭据问题（不是模型调用失败）。"""


def make_provider(cfg: dict[str, Any], client: Any = None) -> ChatProvider:
    """按 [ai] provider 创建服务；client 可注入（测试用假客户端）。"""
    name = cfg["ai"]["provider"]
    if name == "claude":
        from cs2coach.ai.claude import ClaudeProvider

        return ClaudeProvider(cfg["ai"]["claude"], client)
    if name == "deepseek":
        from cs2coach.ai.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider("deepseek", cfg["ai"]["deepseek"], client)
    if name == "local":
        from cs2coach.ai.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider("local", cfg["ai"]["local"], client)
    raise ProviderError(f"未知的 [ai] provider：{name!r}（可选 deepseek、claude、local）")
