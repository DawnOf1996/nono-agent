"""内置工具共享辅助函数 —— 路径解析、输出截断、错误包装。"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult


def resolve_path(context: Any, path: str) -> str:
    """通过上下文解析路径 —— 优先使用路径沙箱。"""
    return str(context.resolve_path(path))


def error_result(tool_name: str, message: str) -> ToolResult:
    """构造 is_error=True 的工具结果。"""
    return ToolResult(
        tool_use_id="",
        content=f"Error: {message}",
        is_error=True,
        tool_name=tool_name,
    )


def ok_result(tool_name: str, content: str) -> ToolResult:
    """构造成功的工具结果。"""
    return ToolResult(
        tool_use_id="",
        content=content,
        tool_name=tool_name,
    )


def truncate_text(text: str, max_chars: int = 30_000) -> str:
    """截断过长文本 —— 保留头部与尾部。"""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + f"\n... [truncated, {len(text)} chars total] ...\n" + tail


def is_binary(data: bytes) -> bool:
    """启发式判断字节内容是否为二进制（含 NUL 字节）。"""
    return b"\x00" in data[:8192]
