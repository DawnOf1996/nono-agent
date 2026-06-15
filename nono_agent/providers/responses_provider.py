"""Responses API Provider（预留骨架）。

这是未来 OpenAI Responses API 集成的占位符。
与 Chat Completions API 的关键差异已在文档字符串中标注。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from nono_agent.core.config import ProviderConfig, ThinkingConfig
from nono_agent.core.types import Message


class ResponsesProvider:
    """OpenAI Responses API 的 LLM Provider（占位符）。

    与 Chat Completions 的关键差异：
    - 使用 `responses.create()` 而非 `chat.completions.create()`
    - 支持内置工具（网页搜索、文件搜索、代码解释器）
    - 不同的流式事件格式
    - 支持多轮推理的响应链接
    """

    name = "responses"          # Provider 名称标识
    supports_thinking = False   # 不支持思考模式
    supports_tool_choice = False # 不支持自定义工具选择

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config  # Provider 配置

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: ThinkingConfig | None = None,
        model: str = "gpt-4o",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Message | Any, None]:
        """通过 OpenAI Responses API 流式获取响应。

        TODO: 待 Responses API 正式发布后实现。
        """
        raise NotImplementedError(
            "Responses API provider is not yet implemented. "
            "Use Chat Completions API (OpenAIProvider) instead."
        )

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """将内部消息转换为 Responses API 格式。

        TODO: 实现格式转换。
        Responses API 使用 'input' 而非 'messages' 的消息格式。
        """
        raise NotImplementedError("Responses API message normalization not yet implemented.")

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """估算 token 数。"""
        from nono_agent.utils.tokens import estimate_messages_tokens

        return estimate_messages_tokens(self.normalize_messages(messages))
