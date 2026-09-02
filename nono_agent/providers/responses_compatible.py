"""Responses 兼容 Provider（非原厂 Responses 网关）。

面向实现 Responses 协议的非 OpenAI 网关，直接用 httpx 走 SSE 流，
复用 `_responses_utils` 的消息格式转换。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

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


class ResponsesCompatibleProvider:
    """Responses 协议的非原厂兼容网关 Provider。"""

    name = "responses_compatible"
    supports_thinking = False
    supports_tool_choice = True

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config
        self._base_url = (config.base_url if config else None) or "https://api.openai.com"
        self._api_key = config.api_key if config else ""
        self._timeout = config.timeout if config else 600.0

    def _endpoint(self) -> str:
        return f"{self._base_url.rstrip('/')}/responses"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

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
        body: dict[str, Any] = {
            "model": model,
            "input": normalize_responses_input(messages),
            "stream": True,
        }
        if system_prompt:
            body["instructions"] = system_prompt
        if tools:
            body["tools"] = [to_responses_tool(t) for t in tools]
        if max_tokens:
            body["max_output_tokens"] = max_tokens

        yield MessageStartEvent(message={"usage": {}})

        next_idx = 0
        text_idx: dict[tuple[int, int], int] = {}
        call_idx: dict[int, int] = {}
        opened: set[int] = set()
        saw_function_call = False
        stop_reason: str | None = None
        usage: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", self._endpoint(), json=body, headers=self._headers()
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise httpx.HTTPStatusError(
                        f"Responses-compatible error {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    if etype == "response.output_item.added":
                        item = event.get("item") or {}
                        oi = event.get("output_index", 0)
                        if item.get("type") == "function_call":
                            call_idx[oi] = next_idx
                            next_idx += 1
                            opened.add(call_idx[oi])
                            saw_function_call = True
                            yield ContentBlockStartEvent(
                                index=call_idx[oi],
                                content_block={
                                    "type": "tool_use",
                                    "id": item.get("call_id", ""),
                                    "name": item.get("name", ""),
                                    "input": "",
                                },
                            )

                    elif etype == "response.content_part.added":
                        part = event.get("part") or {}
                        oi = event.get("output_index", 0)
                        ci = event.get("content_index", 0)
                        if part.get("type") == "output_text":
                            idx = next_idx
                            next_idx += 1
                            text_idx[(oi, ci)] = idx
                            opened.add(idx)
                            yield ContentBlockStartEvent(
                                index=idx, content_block={"type": "text", "text": ""}
                            )

                    elif etype == "response.output_text.delta":
                        oi = event.get("output_index", 0)
                        ci = event.get("content_index", 0)
                        text_block_idx = text_idx.get((oi, ci))
                        if text_block_idx is not None:
                            yield ContentBlockDeltaEvent(
                                index=text_block_idx,
                                delta={"type": "text_delta", "text": event.get("delta", "")},
                            )

                    elif etype == "response.function_call_arguments.delta":
                        oi = event.get("output_index", 0)
                        call_block_idx = call_idx.get(oi)
                        if call_block_idx is not None:
                            yield ContentBlockDeltaEvent(
                                index=call_block_idx,
                                delta={
                                    "type": "input_json_delta",
                                    "partial_json": event.get("delta", ""),
                                },
                            )

                    elif etype in ("response.content_part.done", "response.output_item.done"):
                        oi = event.get("output_index", 0)
                        if etype == "response.content_part.done":
                            ci = event.get("content_index", 0)
                            done_idx = text_idx.get((oi, ci))
                        else:
                            done_idx = call_idx.get(oi)
                        if done_idx is not None and done_idx in opened:
                            opened.discard(done_idx)
                            yield ContentBlockStopEvent(index=done_idx)

                    elif etype == "response.completed":
                        response = event.get("response") or {}
                        status = response.get("status", "completed")
                        if saw_function_call:
                            stop_reason = "tool_use"
                        elif status == "completed":
                            stop_reason = "end_turn"
                        else:
                            stop_reason = "max_tokens"
                        u = response.get("usage") or {}
                        usage = responses_usage_buckets(u)

        for idx in sorted(opened):
            yield ContentBlockStopEvent(index=idx)

        yield MessageDeltaEvent(delta={"stop_reason": stop_reason}, usage=usage)
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
