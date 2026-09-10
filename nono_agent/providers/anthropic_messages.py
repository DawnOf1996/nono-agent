"""Anthropic Messages API Provider 实现。

封装 anthropic SDK 的 AsyncAnthropic 客户端，
在内部 Message/StreamEvent 类型与 Anthropic Messages API 格式之间进行转换。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from nono_agent.core.config import ProviderConfig, ThinkingConfig
from nono_agent.core.types import (
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    Message,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
)
from nono_agent.providers._anthropic_messages_utils import (
    message_delta_usage,
    normalize_anthropic_messages,
    normalize_usage,
)
from nono_agent.providers._openai_chat_completions_utils import deep_merge
from nono_agent.providers._request_log import maybe_log_api_request
from nono_agent.providers.base import FallbackTriggeredError
from nono_agent.utils.tokens import estimate_messages_tokens


class AnthropicMessagesProvider:
    """Anthropic Messages API 的 LLM Provider。

    处理：
    - 消息格式转换（内部类型 -> Anthropic API 格式）
    - 流式响应解析
    - 思考块（扩展思考）
    - 提示缓存（cache_control）
    - 过载时的回退模型切换
    """

    name = "anthropic_messages"
    supports_thinking = True
    supports_tool_choice = True

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config
        self._extra_body: dict[str, Any] = config.extra_body if config else {}
        self._client: Any = None  # 懒初始化的 AsyncAnthropic 客户端
        self._start_usage: dict[str, int] = normalize_usage({})

    def _get_client(self) -> Any:
        """懒初始化 Anthropic 客户端 —— 首次调用时创建。"""
        if self._client is None:
            import anthropic

            kwargs: dict[str, Any] = {}
            if self._config:
                if self._config.api_key:
                    kwargs["api_key"] = self._config.api_key
                if self._config.base_url:
                    kwargs["base_url"] = self._config.base_url
                if self._config.extra_headers:
                    kwargs["default_headers"] = self._config.extra_headers
                kwargs["timeout"] = self._config.timeout
                kwargs["max_retries"] = self._config.max_retries
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: ThinkingConfig | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Message | Any, None]:
        """通过 Anthropic Messages API 流式获取响应。"""
        import anthropic

        client = self._get_client()
        api_messages = self.normalize_messages(messages)

        # 构建 API 请求参数
        # 注意: 使用 client.messages.stream() 时无需传 stream=True，该方法本身就是流式调用
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or 8192,
            "messages": api_messages,
        }

        if system_prompt:
            params["system"] = system_prompt

        if tools:
            params["tools"] = [self._to_anthropic_tool(t) for t in tools]

        # 思考模式配置
        if thinking_config and thinking_config.type != "disabled":
            thinking_params: dict[str, Any] = {"type": "enabled"}
            if thinking_config.budget_tokens:
                thinking_params["budget_tokens"] = thinking_config.budget_tokens
            params["thinking"] = thinking_params

        # 深度合并 extra_body —— 用户配置可覆写或新增任意请求参数
        if self._extra_body:
            params = deep_merge(params, self._extra_body)

        try:
            self._start_usage = normalize_usage({})
            maybe_log_api_request(
                self._config, protocol=self.name, params=params
            )
            async with client.messages.stream(**params) as stream:
                async for event in stream:
                    mapped = self._map_stream_event(event)
                    if mapped is not None:  # 过滤冗余事件（TextEvent 等）
                        yield mapped

        except anthropic.OverloadedError as e:
            # 过载错误 —— 尝试回退到备用模型
            if fallback_model:
                if on_streaming_fallback:
                    on_streaming_fallback()
                raise FallbackTriggeredError(
                    original_model=model,
                    fallback_model=fallback_model,
                    reason=str(e),
                ) from e
            raise

    def _map_stream_event(self, event: Any) -> Any:
        """将 Anthropic SDK 流式事件映射为内部 StreamEvent。

        注意: Anthropic SDK 的 MessageStream 会同时发出两类事件:
        1. 原始 SSE 事件 (RawMessageStartEvent 等) —— 我们转换为内部 StreamEvent
        2. 高层便利事件 (TextEvent, InputJsonEvent 等) —— 包含累积快照，但我们不需要
           因为内容已通过 content_block_delta 累积，这些事件是冗余的，直接过滤。
        """
        event_type = getattr(event, "type", "")

        # 过滤 SDK 高层便利事件 —— 内容已通过原始 SSE 事件累积
        if event_type in ("text", "input_json"):
            return None

        if event_type == "message_start":
            usage = normalize_usage(getattr(event.message, "usage", None))
            self._start_usage = usage
            return MessageStartEvent(message={"usage": usage})

        elif event_type == "content_block_start":
            return ContentBlockStartEvent(
                index=event.index,
                content_block=self._content_block_to_dict(event.content_block),
            )

        elif event_type == "content_block_delta":
            return ContentBlockDeltaEvent(
                index=event.index,
                delta=self._delta_to_dict(event.delta),
            )

        elif event_type == "content_block_stop":
            return ContentBlockStopEvent(index=event.index)

        elif event_type == "message_delta":
            return MessageDeltaEvent(
                delta={"stop_reason": getattr(event.delta, "stop_reason", None)},
                usage=message_delta_usage(
                    self._start_usage, getattr(event, "usage", None)
                ),
            )

        elif event_type == "message_stop":
            return MessageStopEvent()

        # 未知事件类型 —— 原样透传
        return event

    @staticmethod
    def _content_block_to_dict(block: Any) -> dict[str, Any]:
        """将 Anthropic 内容块转换为字典。

        注意：tool_use 块的 input 在流式开始时是空 dict {}，
        转换为空字符串 "" 以便 input_json_delta 能正确累积。
        """
        if hasattr(block, "type"):
            result: dict[str, Any] = {"type": block.type}
            if hasattr(block, "text"):
                result["text"] = block.text
            if hasattr(block, "id"):
                result["id"] = block.id
            if hasattr(block, "name"):
                result["name"] = block.name
            if hasattr(block, "input"):
                # 流式路径：input 初始为 {} 时需转为 "" 以便累积 JSON 片段
                raw_input = block.input
                if isinstance(raw_input, dict) and not raw_input:
                    result["input"] = ""
                else:
                    result["input"] = raw_input
            if hasattr(block, "thinking"):
                result["thinking"] = block.thinking
            if hasattr(block, "signature"):
                result["signature"] = block.signature
            if hasattr(block, "data"):
                result["data"] = block.data
            return result
        return {"type": "unknown"}

    @staticmethod
    def _delta_to_dict(delta: Any) -> dict[str, Any]:
        """将 Anthropic delta 转换为字典。"""
        result: dict[str, Any] = {"type": getattr(delta, "type", "unknown")}
        if hasattr(delta, "text"):
            result["text"] = delta.text
        if hasattr(delta, "partial_json"):
            result["partial_json"] = delta.partial_json
        if hasattr(delta, "thinking"):
            result["thinking"] = delta.thinking
        if hasattr(delta, "signature"):
            result["signature"] = delta.signature
        return result

    @staticmethod
    def _to_anthropic_tool(tool_def: dict[str, Any]) -> dict[str, Any]:
        """将通用工具定义转换为 Anthropic 格式。"""
        return {
            "name": tool_def.get("name", ""),
            "description": tool_def.get("description", ""),
            "input_schema": tool_def.get(
                "input_schema", {"type": "object", "properties": {}}
            ),
        }

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """将内部消息转换为 Anthropic API 格式（共享实现）。"""
        return normalize_anthropic_messages(messages)

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """估算 token 数（使用启发式估算）。"""
        api_messages = self.normalize_messages(messages)
        return estimate_messages_tokens(api_messages) + len(system_prompt) // 4
