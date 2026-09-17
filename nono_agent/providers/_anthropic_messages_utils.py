"""Anthropic Messages 协议公共工具。

供 ``AnthropicMessagesProvider`` 复用消息格式转换与 usage 归一。
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import Message


def normalize_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部消息转换为 Anthropic Messages API 格式。

    处理 tool_use.input 从 JSON 字符串到 dict 的转换 ——
    流式累积后 input 是字符串，但 Messages API 要求 dict。
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "message") and isinstance(msg.message, dict):
            api_msg = msg.message.copy()
            if "role" not in api_msg:
                api_msg["role"] = (
                    msg.type if msg.type in ("user", "assistant") else "user"
                )

            content = api_msg.get("content")
            if isinstance(content, list) and api_msg.get("role") == "assistant":
                new_content = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        block = {**block}  # 浅拷贝避免污染原始数据
                        raw_input = block.get("input", {})
                        if isinstance(raw_input, str):
                            try:
                                block["input"] = (
                                    json.loads(raw_input) if raw_input.strip() else {}
                                )
                            except json.JSONDecodeError:
                                block["input"] = {}
                        elif not isinstance(raw_input, dict):
                            block["input"] = {}
                    new_content.append(block)
                api_msg["content"] = new_content

            result.append(api_msg)
    return result


def to_anthropic_tool(tool_def: dict[str, Any]) -> dict[str, Any]:
    """将通用工具定义转换为 Anthropic Messages 格式。"""
    return {
        "name": tool_def.get("name", ""),
        "description": tool_def.get("description", ""),
        "input_schema": tool_def.get(
            "input_schema", {"type": "object", "properties": {}}
        ),
    }


def anthropic_content_block_to_dict(block: Any) -> dict[str, Any]:
    """将 Anthropic 内容块（SDK 对象或 dict）转换为字典。

    注意：tool_use 块的 input 在流式开始时是空 dict {}，
    转换为空字符串 "" 以便 input_json_delta 能正确累积。
    """
    if isinstance(block, dict):
        result: dict[str, Any] = {"type": block.get("type", "unknown")}
        for key in ("text", "id", "name", "input", "thinking", "signature", "data"):
            if key in block:
                value = block[key]
                if key == "input" and isinstance(value, dict) and not value:
                    result["input"] = ""
                else:
                    result[key] = value
        return result

    if hasattr(block, "type"):
        result = {"type": block.type}
        if hasattr(block, "text"):
            result["text"] = block.text
        if hasattr(block, "id"):
            result["id"] = block.id
        if hasattr(block, "name"):
            result["name"] = block.name
        if hasattr(block, "input"):
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


def anthropic_delta_to_dict(delta: Any) -> dict[str, Any]:
    """将 Anthropic delta（SDK 对象或 dict）转换为字典。"""
    if isinstance(delta, dict):
        return dict(delta)
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


_START_FILLABLE_USAGE_KEYS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _usage_int(raw: Any, key: str) -> int:
    if raw is None:
        return 0
    if isinstance(raw, dict):
        return int(raw.get(key, 0) or 0)
    return int(getattr(raw, key, 0) or 0)


def normalize_usage(raw: Any) -> dict[str, int]:
    """把 usage 对象收成四类 token 计数；缺省或 None 视为 0。"""
    return {
        "input_tokens": _usage_int(raw, "input_tokens"),
        "output_tokens": _usage_int(raw, "output_tokens"),
        "cache_read_input_tokens": _usage_int(raw, "cache_read_input_tokens"),
        "cache_creation_input_tokens": _usage_int(raw, "cache_creation_input_tokens"),
    }


def message_delta_usage(start_usage: dict[str, int], delta_raw: Any) -> dict[str, int]:
    """message_delta 的 usage：始终带 output；start 为 0 时回填 input/cache。

    MiniMax 等网关在 message_start 里把 input/cache 填 0，真实值放在 message_delta。
    """
    delta_usage = normalize_usage(delta_raw)
    mapped: dict[str, int] = {"output_tokens": delta_usage["output_tokens"]}
    for key in _START_FILLABLE_USAGE_KEYS:
        if start_usage.get(key, 0) == 0 and delta_usage[key]:
            mapped[key] = delta_usage[key]
    return mapped
