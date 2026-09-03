"""OpenAI Chat Completions API Provider。

封装 openai SDK 的 AsyncOpenAI 客户端（``chat.completions``），
通过 api_key + base_url 接入原厂或兼容端点。
"""

from __future__ import annotations

import logging
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
from nono_agent.providers._openai_chat_completions_utils import (
    normalize_openai_messages,
    openai_chat_usage_buckets,
    to_openai_tool,
)
from nono_agent.providers.base import FallbackTriggeredError
from nono_agent.utils.tokens import estimate_messages_tokens

logger = logging.getLogger(__name__)


class OpenAIChatCompletionsProvider:
    """OpenAI Chat Completions API 的 LLM Provider。

    处理：
    - 消息格式转换（内部类型 -> OpenAI API 格式）
    - 流式响应解析（delta chunks -> StreamEvent）
    - 函数调用 -> tool_use 映射
    - 推理 token（o1/o3 系列模型）
    """

    name = "openai_chat_completions"
    supports_thinking = False
    supports_tool_choice = True

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config
        self._extra_body: dict[str, Any] = config.extra_body if config else {}
        self._client: Any = None  # 懒初始化的 AsyncOpenAI 客户端

    def _get_client(self) -> Any:
        """懒初始化 OpenAI 客户端 —— 首次调用时创建。"""
        if self._client is None:
            import openai

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
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

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
        """通过 OpenAI Chat Completions API 流式获取响应。"""
        import openai

        client = self._get_client()
        api_messages = self.normalize_messages(messages)

        # 前置系统提示（OpenAI 使用 system role）
        if system_prompt:
            api_messages = [
                {"role": "system", "content": system_prompt},
                *api_messages,
            ]

        params: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if max_tokens:
            params["max_tokens"] = max_tokens

        if tools:
            params["tools"] = [to_openai_tool(t) for t in tools]

        # 结构化输出：json_schema 配置 → response_format
        json_schema = kwargs.get("json_schema")
        if json_schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": json_schema,
                    "strict": True,
                },
            }

        # 将 extra_body 通过 SDK 专用通道传入，避免非标准参数触发 TypeError
        if self._extra_body:
            params["extra_body"] = self._extra_body

        try:
            logger.debug(
                "Stream API call: model=%s, messages=%d, tools=%d",
                model, len(api_messages), len(tools) if tools else 0,
            )
            stream = await client.chat.completions.create(**params)
            yield MessageStartEvent(message={"usage": {}})

            # 内容块索引跟踪 —— 支持 thinking / text / tool_use 混合排列
            next_block_idx = 0
            opened_blocks: set[int] = set()
            thinking_idx: int | None = None
            text_idx: int | None = None
            thinking_closed = False  # 防止迟到的 reasoning_content 追加到正文之后

            async for chunk in stream:
                if not chunk.choices:
                    # 仅包含 usage 的最终 chunk
                    if chunk.usage:
                        yield MessageDeltaEvent(
                            delta={"stop_reason": None},
                            usage=openai_chat_usage_buckets(chunk.usage),
                        )
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # 文本内容 —— 优先处理，以便在正文到达时立即关闭思考块
                if delta.content:
                    if text_idx is None:
                        # 正文首次到达：立即关闭思考块（防止后续 reasoning_content 交错）
                        if thinking_idx is not None and not thinking_closed:
                            yield ContentBlockStopEvent(index=thinking_idx)
                            opened_blocks.discard(thinking_idx)
                            thinking_closed = True
                        text_idx = next_block_idx
                        next_block_idx += 1
                        opened_blocks.add(text_idx)
                        yield ContentBlockStartEvent(
                            index=text_idx,
                            content_block={"type": "text", "text": ""},
                        )
                    yield ContentBlockDeltaEvent(
                        index=text_idx,
                        delta={"type": "text_delta", "text": delta.content},
                    )

                # 思考/推理内容（部分厂商通过 reasoning_content 返回）
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and not thinking_closed:
                    if thinking_idx is None:
                        thinking_idx = next_block_idx
                        next_block_idx += 1
                        opened_blocks.add(thinking_idx)
                        yield ContentBlockStartEvent(
                            index=thinking_idx,
                            content_block={"type": "thinking", "thinking": ""},
                        )
                    yield ContentBlockDeltaEvent(
                        index=thinking_idx,
                        delta={"type": "thinking_delta", "thinking": reasoning},
                    )

                # 工具调用
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index + next_block_idx
                        if tc.id:
                            # 新工具调用开始 —— input 初始化为空字符串，流式累积 JSON 片段
                            opened_blocks.add(idx)
                            yield ContentBlockStartEvent(
                                index=idx,
                                content_block={
                                    "type": "tool_use",
                                    "id": tc.id,
                                    "name": tc.function.name or "",
                                    "input": "",
                                },
                            )
                        if tc.function and tc.function.arguments:
                            yield ContentBlockDeltaEvent(
                                index=idx,
                                delta={
                                    "type": "input_json_delta",
                                    "partial_json": tc.function.arguments,
                                },
                            )

                # 停止原因
                if choice.finish_reason:
                    stop_reason = "end_turn"
                    if choice.finish_reason == "tool_calls":
                        stop_reason = "tool_use"
                    yield MessageDeltaEvent(
                        delta={"stop_reason": stop_reason},
                        usage={},
                    )

            # 仅关闭已打开的内容块
            for idx in sorted(opened_blocks):
                yield ContentBlockStopEvent(index=idx)

            yield MessageStopEvent()

        except openai.RateLimitError as e:
            # 速率限制错误 —— 尝试回退到备用模型
            if fallback_model:
                if on_streaming_fallback:
                    on_streaming_fallback()
                raise FallbackTriggeredError(
                    original_model=model,
                    fallback_model=fallback_model,
                    reason=str(e),
                ) from e
            raise

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """将内部消息转换为 OpenAI Chat Completions 格式。

        委托给 _openai_chat_completions_utils.normalize_openai_messages 公共实现。
        """
        return normalize_openai_messages(messages)

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """估算 token 数。"""
        api_messages = self.normalize_messages(messages)
        return estimate_messages_tokens(api_messages) + len(system_prompt) // 4
