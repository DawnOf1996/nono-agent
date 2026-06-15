"""通用 OpenAI 兼容 Provider 实现。

适配所有使用 OpenAI Chat Completions 协议的第三方厂商，
通过 base_url + api_key + extra_body 配置即可接入，无需编写新 Provider。

支持的厂商（示例）：
- MiniMax (https://api.minimax.chat/v1)
- DeepSeek (https://api.deepseek.com)
- 通义千问 (https://dashscope.aliyuncs.com/compatible-mode/v1)
- 智谱 GLM (https://open.bigmodel.cn/api/paas/v4)
- Moonshot / Kimi (https://api.moonshot.cn/v1)
- Groq (https://api.groq.com/openai/v1)
- Together AI (https://api.together.xyz/v1)
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
from nono_agent.providers._openai_utils import (
    normalize_openai_messages,
    to_openai_tool,
)
from nono_agent.providers.base import FallbackTriggeredError
from nono_agent.utils.tokens import estimate_messages_tokens

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """通用 OpenAI 兼容 Provider —— 适配所有使用 OpenAI Chat Completions 协议的厂商。

    与 OpenAIProvider 的区别：
    - OpenAIProvider 面向 OpenAI 官方 API（可能有官方特有功能如 reasoning tokens）
    - OpenAICompatibleProvider 面向第三方兼容厂商，更通用
    - 两者共享 _openai_utils 中的消息格式转换逻辑

    配置示例::

        ProviderConfig(
            model_id="MiniMax-M3",
            adapter="openai",
            api_key="your-minimax-key",
            base_url="https://api.minimax.chat/v1",
            extra_body={"stream_increments": True},
        )
    """

    name = "openai_compatible"      # Provider 名称标识
    supports_thinking = False       # 第三方兼容 API 通常不支持思考模式
    supports_tool_choice = True     # 支持工具选择（OpenAI 协议标准能力）

    def __init__(self, config: ProviderConfig | None = None) -> None:
        """初始化 OpenAI 兼容 Provider。

        Args:
            config: Provider 配置，必须包含 base_url 指向厂商的 OpenAI 兼容端点
        """
        self._config = config
        self._extra_body: dict[str, Any] = config.extra_body if config else {}
        self._client: Any = None  # 懒初始化的 openai.AsyncOpenAI 客户端

    def _get_client(self) -> Any:
        """懒初始化 OpenAI 客户端 —— 使用厂商的 base_url。"""
        if self._client is None:
            import openai

            kwargs: dict[str, Any] = {}
            if self._config:
                if self._config.api_key:
                    kwargs["api_key"] = self._config.api_key
                if self._config.base_url:
                    kwargs["base_url"] = self._config.base_url
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
        model: str = "",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Message | Any, None]:
        """通过 OpenAI 兼容 API 流式获取响应。

        与 OpenAIProvider.stream() 的核心区别：
        - 使用厂商的 base_url（通过 config 配置）
        - 支持 extra_body 深度合并到请求参数中
        """
        import openai

        client = self._get_client()
        api_messages = self.normalize_messages(messages)

        # 前置系统提示（OpenAI 协议使用 system role）
        if system_prompt:
            api_messages = [
                {"role": "system", "content": system_prompt},
                *api_messages,
            ]

        # 构建标准 OpenAI 请求参数
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

        # 将 extra_body 通过 SDK 专用通道传入，避免非标准参数触发 TypeError
        if self._extra_body:
            params["extra_body"] = self._extra_body

        try:
            logger.debug(
                "Stream API call: model=%s, messages=%d, tools=%d, extra_body=%s",
                model, len(api_messages), len(tools) if tools else 0,
                list(self._extra_body.keys()) if self._extra_body else None,
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
                        logger.debug(
                            "Stream usage: input=%d, output=%d",
                            chunk.usage.prompt_tokens, chunk.usage.completion_tokens,
                        )
                        yield MessageDeltaEvent(
                            delta={"stop_reason": None},
                            usage={
                                "output_tokens": chunk.usage.completion_tokens,
                                "input_tokens": chunk.usage.prompt_tokens,
                            },
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

                # 思考/推理内容（MiniMax 等厂商通过 reasoning_content 返回）
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
        """将内部消息转换为 OpenAI Chat Completions 格式。"""
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
