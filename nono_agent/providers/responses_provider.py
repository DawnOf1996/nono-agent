"""OpenAI Responses API Provider（原厂）。

用 OpenAI SDK 的 ``client.responses.create(stream=True)`` 实现，
把 Responses 流式事件映射为内部 StreamEvent 协议。
消息格式转换见 `_responses_utils`（供原厂与兼容层复用）。
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
from nono_agent.providers._responses_utils import (
    normalize_responses_input,
    responses_usage_buckets,
    to_responses_tool,
)
from nono_agent.utils.tokens import estimate_messages_tokens


class ResponsesProvider:
    """OpenAI Responses API 的 LLM Provider（原厂）。"""

    name = "responses"
    supports_thinking = False
    supports_tool_choice = True

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config
        self._client: Any = None

    def _get_client(self) -> Any:
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
        model: str = "gpt-4o",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Message | Any, None]:
        """通过 Responses API 流式获取响应，映射为内部 StreamEvent。"""
        client = self._get_client()
        input_items = normalize_responses_input(messages)

        params: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
        }
        if system_prompt:
            params["instructions"] = system_prompt
        if tools:
            params["tools"] = [to_responses_tool(t) for t in tools]
        if max_tokens:
            params["max_output_tokens"] = max_tokens

        yield MessageStartEvent(message={"usage": {}})

        next_idx = 0
        text_idx: dict[tuple[int, int], int] = {}   # (output_index, content_index) -> block idx
        call_idx: dict[int, int] = {}               # output_index -> block idx
        opened: set[int] = set()
        saw_function_call = False
        stop_reason: str | None = None
        usage: dict[str, Any] = {}

        async for event in client.responses.create(**params):
            etype = getattr(event, "type", "")

            if etype == "response.output_item.added":
                item = getattr(event, "item", None)
                oi = getattr(event, "output_index", 0)
                itype = getattr(item, "type", None)
                if itype == "function_call":
                    call_idx[oi] = next_idx
                    next_idx += 1
                    opened.add(call_idx[oi])
                    saw_function_call = True
                    yield ContentBlockStartEvent(
                        index=call_idx[oi],
                        content_block={
                            "type": "tool_use",
                            "id": getattr(item, "call_id", ""),
                            "name": getattr(item, "name", ""),
                            "input": "",
                        },
                    )

            elif etype == "response.content_part.added":
                part = getattr(event, "part", None)
                oi = getattr(event, "output_index", 0)
                ci = getattr(event, "content_index", 0)
                if getattr(part, "type", None) == "output_text":
                    idx = next_idx
                    next_idx += 1
                    text_idx[(oi, ci)] = idx
                    opened.add(idx)
                    yield ContentBlockStartEvent(
                        index=idx, content_block={"type": "text", "text": ""}
                    )

            elif etype == "response.output_text.delta":
                oi = getattr(event, "output_index", 0)
                ci = getattr(event, "content_index", 0)
                text_block_idx = text_idx.get((oi, ci))
                if text_block_idx is not None:
                    yield ContentBlockDeltaEvent(
                        index=text_block_idx,
                        delta={"type": "text_delta", "text": getattr(event, "delta", "")},
                    )

            elif etype == "response.function_call_arguments.delta":
                oi = getattr(event, "output_index", 0)
                call_block_idx = call_idx.get(oi)
                if call_block_idx is not None:
                    yield ContentBlockDeltaEvent(
                        index=call_block_idx,
                        delta={
                            "type": "input_json_delta",
                            "partial_json": getattr(event, "delta", ""),
                        },
                    )

            elif etype in ("response.content_part.done", "response.output_item.done"):
                oi = getattr(event, "output_index", 0)
                if etype == "response.content_part.done":
                    ci = getattr(event, "content_index", 0)
                    done_idx = text_idx.get((oi, ci))
                else:
                    done_idx = call_idx.get(oi)
                if done_idx is not None and done_idx in opened:
                    opened.discard(done_idx)
                    yield ContentBlockStopEvent(index=done_idx)

            elif etype == "response.completed":
                response = getattr(event, "response", None)
                status = getattr(response, "status", "completed")
                if saw_function_call:
                    stop_reason = "tool_use"
                elif status == "completed":
                    stop_reason = "end_turn"
                else:
                    stop_reason = "max_tokens"

                usage_obj = getattr(response, "usage", None)
                if usage_obj is not None:
                    usage = responses_usage_buckets(usage_obj)

        # 关闭任何仍打开的内容块
        for idx in sorted(opened):
            yield ContentBlockStopEvent(index=idx)

        yield MessageDeltaEvent(
            delta={"stop_reason": stop_reason}, usage=usage
        )
        yield MessageStopEvent()

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        return normalize_responses_input(messages)

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        return estimate_messages_tokens(
            [m.message for m in messages if hasattr(m, "message")]
        ) + len(system_prompt) // 4
