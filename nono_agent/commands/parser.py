"""Slash 命令解析 —— 与自然语言分流。"""

from __future__ import annotations


def parse_slash(text: str) -> tuple[str, list[str]] | None:
    """解析斜杠命令。

    以 ``/`` 开头的输入视为命令，返回 ``(name, args)``；
    否则返回 None（自然语言）。
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split()
    if not parts:
        return None
    return parts[0], parts[1:]


def is_slash_command(text: str) -> bool:
    """判断输入是否为斜杠命令。"""
    return parse_slash(text) is not None
