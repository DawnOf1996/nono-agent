"""OpenAI Responses 协议公共工具。

供 ``OpenAIResponsesProvider`` 复用消息格式与工具转换。
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import Message
from nono_agent.providers._openai_chat_completions_utils import (
    image_url_from_block,
    usage_to_buckets,
)


def responses_usage_buckets(usage: Any) -> dict[str, int]:
    """从 Responses API 的 usage（SDK 对象或兼容 dict）提取统一四桶。

    读取 ``input_tokens`` / ``output_tokens`` 与
    ``input_tokens_details.cached_tokens``，换算成统一口径（input=未缓存）。
    """
    if isinstance(usage, dict):
        total_input = usage.get("input_tokens", 0) or 0
        total_output = usage.get("output_tokens", 0) or 0
        details = usage.get("input_tokens_details") or {}
        cached = details.get("cached_tokens", 0) or 0
    else:
        total_input = getattr(usage, "input_tokens", 0) or 0
        total_output = getattr(usage, "output_tokens", 0) or 0
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    return usage_to_buckets(total_input, total_output, cached)


def normalize_responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部消息转换为 Responses API 的 ``input`` items 列表。

    - user 文本块 → ``message`` (role=user, input_text)
    - user 图片块 → ``input_image``
    - user tool_result → ``function_call_output``
    - assistant 文本块 → ``message`` (role=assistant, output_text)
    - assistant tool_use → ``function_call``
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        if not hasattr(msg, "message") or not isinstance(msg.message, dict):
            continue
        role = msg.message.get("role", msg.type)
        content = msg.message.get("content", [])
        if isinstance(content, str):
            text = content
            if not text:
                continue
            if role == "assistant":
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            else:
                items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                )
            continue
        if not isinstance(content, list):
            continue

        user_parts: list[dict[str, Any]] = []

        def flush_user() -> None:
            if user_parts:
                items.append(
                    {"type": "message", "role": "user", "content": list(user_parts)}
                )
                user_parts.clear()

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")

            if btype == "tool_result":
                flush_user()
                output = block.get("content", "")
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.get("tool_use_id", ""),
                        "output": output if isinstance(output, str) else json.dumps(output),
                    }
                )
            elif role == "user" and btype == "text":
                user_parts.append({"type": "input_text", "text": block.get("text", "")})
            elif role == "user" and btype in ("image", "image_url"):
                url = image_url_from_block(block)
                if url:
                    user_parts.append({"type": "input_image", "image_url": url})
            elif role == "assistant" and btype == "text":
                flush_user()
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": block.get("text", "")}],
                    }
                )
            elif role == "assistant" and btype == "tool_use":
                flush_user()
                raw_input = block.get("input", {})
                arguments = (
                    raw_input
                    if isinstance(raw_input, str)
                    else json.dumps(raw_input, ensure_ascii=False)
                )
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": arguments,
                    }
                )
        flush_user()

    return items


def to_responses_tool(tool_def: dict[str, Any]) -> dict[str, Any]:
    """将通用工具定义转换为 Responses function 格式。"""
    return {
        "type": "function",
        "name": tool_def.get("name", ""),
        "description": tool_def.get("description", ""),
        "parameters": tool_def.get(
            "input_schema", {"type": "object", "properties": {}}
        ),
    }
