"""Anthropic Messages 协议公共工具 —— 原厂与兼容网关共享。

供 ``AnthropicProvider``（原厂）与 ``MessagesCompatibleProvider``（非原厂）
复用消息格式转换与流事件映射。
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
