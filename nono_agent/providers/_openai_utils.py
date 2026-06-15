"""OpenAI 消息格式转换公共工具函数。

OpenAIProvider 和 OpenAICompatibleProvider 共享的消息格式转换逻辑，
提取为公共函数避免代码重复。

包含：
- normalize_openai_messages: 内部消息 → OpenAI Chat Completions 格式
- to_openai_tool: 通用工具定义 → OpenAI function 格式
- deep_merge: 字典深度合并（用于 extra_body 注入）
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import Message


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典 —— override 中的值覆盖 base 中的同名键。

    嵌套字典会递归合并，而非简单覆盖。
    用于将 extra_body 注入 API 请求参数。

    Args:
        base: 基础字典（不被修改）
        override: 覆盖字典（其中的值优先）

    Returns:
        合并后的新字典
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def normalize_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部消息转换为 OpenAI Chat Completions 格式。

    特殊处理：
    - 将 assistant 消息中的 tool_use 块转换为 OpenAI tool_calls 格式
    - 将 tool_result 类型的 user 消息映射为 OpenAI 的 tool role 消息
    - 确保每条消息都有正确的 role 字段
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "message") and isinstance(msg.message, dict):
            api_msg = msg.message.copy()
            role = api_msg.get("role", msg.type)
            content = api_msg.get("content", [])
            if isinstance(content, list):
                # assistant 消息: 分离 tool_use 块为 tool_calls
                if role == "assistant":
                    text_content: list[dict] = []
                    tool_calls: list[dict] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            # 将 tool_use 块转换为 OpenAI tool_call 格式
                            raw_input = block.get("input", {})
                            if isinstance(raw_input, str):
                                arguments = raw_input
                            elif isinstance(raw_input, dict):
                                arguments = json.dumps(raw_input, ensure_ascii=False)
                            else:
                                arguments = str(raw_input)
                            tool_calls.append(
                                {
                                    "id": block.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": block.get("name", ""),
                                        "arguments": arguments,
                                    },
                                }
                            )
                        else:
                            text_content.append(block)

                    # 组装 assistant 消息: 文本内容 + tool_calls
                    if text_content:
                        # 提取纯文本拼接作为 content
                        text_parts = [
                            b.get("text", "")
                            for b in text_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        api_msg["content"] = "".join(text_parts) or None
                    else:
                        api_msg["content"] = None
                    if tool_calls:
                        api_msg["tool_calls"] = tool_calls
                    api_msg["role"] = role
                    # 移除无关字段
                    api_msg.pop("usage", None)
                    api_msg.pop("stop_reason", None)
                    result.append(api_msg)

                # user 消息: 分离 tool_result 块为 tool role 消息
                else:
                    new_content = []
                    tool_messages = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "tool_result":
                                tool_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": block.get("tool_use_id", ""),
                                        "content": block.get("content", ""),
                                    }
                                )
                            else:
                                new_content.append(block)
                    if new_content:
                        api_msg["content"] = new_content
                        api_msg["role"] = role
                        result.append(api_msg)
                    result.extend(tool_messages)
            else:
                api_msg["role"] = role
                # 移除无关字段
                api_msg.pop("usage", None)
                api_msg.pop("stop_reason", None)
                result.append(api_msg)
    return result


def to_openai_tool(tool_def: dict[str, Any]) -> dict[str, Any]:
    """将通用工具定义转换为 OpenAI function 格式。

    Args:
        tool_def: 通用工具定义（包含 name/description/input_schema）

    Returns:
        OpenAI function calling 格式的工具定义
    """
    return {
        "type": "function",
        "function": {
            "name": tool_def.get("name", ""),
            "description": tool_def.get("description", ""),
            "parameters": tool_def.get(
                "input_schema", {"type": "object", "properties": {}}
            ),
        },
    }
