"""Anthropic Messages 兼容 Provider（非原厂网关）。

面向实现 Messages 协议但非 Anthropic 官方的网关（如 MiniMax 的
``/anthropic`` 端点）。直接用 httpx 走 SSE 流，避免依赖官方 SDK 的
严格行为，对第三方字段缺失更宽容。

与 ``AnthropicProvider``（原厂）共享 `_messages_utils` 的消息格式转换。
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
from nono_agent.providers._messages_utils import (
    anthropic_content_block_to_dict,
    anthropic_delta_to_dict,
    normalize_anthropic_messages,
    to_anthropic_tool,
)
from nono_agent.providers.base import FallbackTriggeredError
from nono_agent.utils.tokens import estimate_messages_tokens

# loop 从 message_start 取 input/cache，从 message_delta 累加 output。
# MiniMax 等网关在 start 里把这些字段填 0，真实值放在 message_delta.usage。
_START_FILLABLE_USAGE_KEYS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _normalize_usage(raw: dict[str, Any]) -> dict[str, int]:
    """把网关 usage 对象收成四类 token 计数；缺省或 None 视为 0。"""
    return {
        "input_tokens": int(raw.get("input_tokens", 0) or 0),
        "output_tokens": int(raw.get("output_tokens", 0) or 0),
        "cache_read_input_tokens": int(raw.get("cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(raw.get("cache_creation_input_tokens", 0) or 0),
    }


class MessagesCompatibleProvider:
    """Anthropic Messages 协议的非原厂兼容网关 Provider。"""

    name = "messages_compatible"
    supports_thinking = False
    supports_tool_choice = True

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config
        self._base_url = (config.base_url if config else None) or "https://api.anthropic.com"
        self._api_key = config.api_key if config else ""
        self._timeout = config.timeout if config else 600.0
        self._extra_body: dict[str, Any] = config.extra_body if config else {}
        self._extra_headers: dict[str, str] = config.extra_headers if config else {}
        # 本轮 message_start 的 usage；message_delta 用它判断哪些字段要回填
        self._start_usage: dict[str, int] = _normalize_usage({})

    def _endpoint(self) -> str:
        base = self._base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
            headers["authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        return headers

    def _build_body(
        self,
        model: str,
        max_tokens: int | None,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """构建请求体（含 stream=True，使网关返回 SSE 流）。"""
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or 8192,
            "messages": normalize_anthropic_messages(messages),
            "stream": True,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = [to_anthropic_tool(t) for t in tools]
        if self._extra_body:
            body.update(self._extra_body)
        return body

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
        """通过 httpx 流式调用 Messages 兼容端点，映射为内部 StreamEvent。"""
        self._start_usage = _normalize_usage({})
        body = self._build_body(model, max_tokens, messages, system_prompt, tools)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    self._endpoint(),
                    json=body,
                    headers=self._headers(),
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        raise _status_error(resp.status_code, resp.text)

                    current_event_type: str | None = None
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        # SSE 事件类型可能在 event: 行，也可能在 data JSON 内
                        if line.startswith("event:"):
                            current_event_type = line[len("event:") :].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if "type" not in event and current_event_type:
                            event["type"] = current_event_type

                        mapped = self._map_event(event)
                        if mapped is not None:
                            yield mapped
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 529 and fallback_model:
                if on_streaming_fallback:
                    on_streaming_fallback()
                raise FallbackTriggeredError(
                    original_model=model,
                    fallback_model=fallback_model,
                    reason=str(exc),
                ) from exc
            raise

    def _map_event(self, event: dict[str, Any]) -> Any:
        event_type = event.get("type", "")

        if event_type == "message_start":
            usage = _normalize_usage((event.get("message") or {}).get("usage") or {})
            self._start_usage = usage
            return MessageStartEvent(message={"usage": usage})

        if event_type == "content_block_start":
            return ContentBlockStartEvent(
                index=event.get("index", 0),
                content_block=anthropic_content_block_to_dict(event.get("content_block", {})),
            )

        if event_type == "content_block_delta":
            return ContentBlockDeltaEvent(
                index=event.get("index", 0),
                delta=anthropic_delta_to_dict(event.get("delta", {})),
            )

        if event_type == "content_block_stop":
            return ContentBlockStopEvent(index=event.get("index", 0))

        if event_type == "message_delta":
            delta = event.get("delta") or {}
            delta_usage = _normalize_usage(event.get("usage") or {})
            mapped_usage: dict[str, int] = {
                "output_tokens": delta_usage["output_tokens"],
            }
            # start 已给出的 input/cache 由 loop 在 message_start 写入；
            # start 为 0 时改从本事件回填，供 loop 做 usage + Usage(**event.usage)。
            for key in _START_FILLABLE_USAGE_KEYS:
                if self._start_usage.get(key, 0) == 0 and delta_usage[key]:
                    mapped_usage[key] = delta_usage[key]
            return MessageDeltaEvent(
                delta={"stop_reason": delta.get("stop_reason")},
                usage=mapped_usage,
            )

        if event_type == "message_stop":
            return MessageStopEvent()

        if event_type == "error":
            raise _status_error(400, str(event))

        return None

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        return normalize_anthropic_messages(messages)

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        api_messages = self.normalize_messages(messages)
        return estimate_messages_tokens(api_messages) + len(system_prompt) // 4


def _status_error(status_code: int, text: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://messages-compatible.invalid")
    response = httpx.Response(status_code, text=text, request=request)
    return httpx.HTTPStatusError(
        f"Messages-compatible API error {status_code}: {text[:200]}",
        request=request,
        response=response,
    )
