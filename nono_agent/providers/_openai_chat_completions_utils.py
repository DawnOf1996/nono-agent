"""OpenAI 消息格式转换公共工具函数。

供 Chat Completions Provider 复用消息格式转换逻辑。

包含：
- normalize_openai_messages: 内部消息 → OpenAI Chat Completions 格式
- to_openai_tool: 通用工具定义 → OpenAI function 格式
- deep_merge: 字典深度合并（用于 extra_body 注入）
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import Message


def usage_to_buckets(
    total_input: int | None,
    total_output: int | None,
    cached_input: int | None = None,
) -> dict[str, int]:
    """把 OpenAI 系「含缓存的总输入」换算为本项目统一的四桶口径。

    OpenAI 的 ``prompt_tokens`` / ``input_tokens`` 是**含缓存**的总输入，
    ``cached_tokens``（``*_details.cached_tokens``）是其中的缓存命中子集；
    本项目（对齐 Anthropic）的 ``input_tokens`` 表示**未命中缓存的新鲜输入**。

    换算：
      - input_tokens             = 总输入 - 缓存命中（≥0）
      - cache_read_input_tokens  = 缓存命中
      - cache_creation_input_tokens = 0（OpenAI 不单独上报缓存写入）
      - output_tokens            = 原样透传
    """
    cached = max(0, int(cached_input or 0))
    fresh = max(0, int(total_input or 0) - cached)
    return {
        "input_tokens": fresh,
        "output_tokens": int(total_output or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": 0,
    }


def openai_chat_usage_buckets(usage: Any) -> dict[str, int]:
    """从 OpenAI Chat Completions 的 usage 对象（SDK 或兼容对象）提取四桶。

    读取 ``prompt_tokens`` / ``completion_tokens`` 与
    ``prompt_tokens_details.cached_tokens``，换算成统一口径。
    """
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return usage_to_buckets(
        getattr(usage, "prompt_tokens", 0),
        getattr(usage, "completion_tokens", 0),
        cached,
    )


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


def image_url_from_block(block: dict[str, Any]) -> str | None:
    """从 Anthropic ``image`` 块或 OpenAI ``image_url`` 块提取 URL。"""
    btype = block.get("type")
    if btype == "image_url":
        image_url = block.get("image_url")
        if isinstance(image_url, str) and image_url:
            return image_url
        if isinstance(image_url, dict):
            url = image_url.get("url")
            return url if isinstance(url, str) and url else None
    source = block.get("source")
    if isinstance(source, dict):
        if source.get("type") == "base64" and source.get("data"):
            media = source.get("media_type") or "image/png"
            return f"data:{media};base64,{source['data']}"
        url = source.get("url")
        if isinstance(url, str) and url:
            return url
    url = block.get("url")
    return url if isinstance(url, str) and url else None


def _openai_user_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """将内部用户内容块转为 Chat Completions 多模态格式。"""
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text") or ""}
    if btype in ("image", "image_url"):
        url = image_url_from_block(block)
        if not url:
            return None
        return {"type": "image_url", "image_url": {"url": url}}
    return block


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
                    text_content: list[dict[str, Any]] = []
                    tool_calls: list[dict[str, Any]] = []
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
                                converted = _openai_user_block(block)
                                if converted is not None:
                                    new_content.append(converted)
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
