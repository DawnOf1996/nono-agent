"""内置工具共享辅助函数 —— 路径解析、输出截断、错误包装、文本解码。"""

from __future__ import annotations

import locale
from typing import Any

from nono_agent.core.types import ToolResult

# 所有 strict 候选均失败时的占位编码名（utf-8 + replace）
LOSSY_ENCODING = "utf-8-replace"

_UTF8_FAMILY = frozenset({"utf-8", "utf8", "utf-8-sig"})
_ASCII_FAMILY = frozenset({"ascii", "us-ascii", "ansi-x3.4-1968"})


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


def _normalize_encoding_name(name: str) -> str:
    return name.lower().replace("_", "-")


def is_utf8_family(encoding: str) -> bool:
    """是否为 UTF-8 系（含 BOM）。"""
    return _normalize_encoding_name(encoding) in _UTF8_FAMILY


def encoding_note(encoding: str) -> str | None:
    """非 UTF-8 解码时给模型看的一行说明；UTF-8 返回 None。"""
    if is_utf8_family(encoding):
        return None
    if encoding == LOSSY_ENCODING:
        return (
            "[decoded as utf-8 with replacement characters; source encoding is uncertain]"
        )
    return f"[decoded as {encoding}]"


def _decode_candidates() -> list[str]:
    """UTF-8 strict → 本机默认编码 → gb18030。去重、跳过 ASCII。"""
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = _normalize_encoding_name(name)
        if not name or key in seen or key in _ASCII_FAMILY:
            return
        seen.add(key)
        names.append(name)

    add("utf-8-sig")
    add("utf-8")
    try:
        preferred = locale.getpreferredencoding(False) or ""
    except (OSError, ValueError):
        preferred = ""
    add(preferred)
    add("gb18030")
    return names


def decode_text(data: bytes) -> tuple[str, str]:
    """把字节解码为文本。

    先 utf-8-sig / utf-8（strict），失败再试本机默认编码与 gb18030；
    仍失败则 utf-8 ``errors=replace``，编码名记为 ``utf-8-replace``。
    """
    if not data:
        return "", "utf-8"
    for encoding in _decode_candidates():
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), LOSSY_ENCODING
