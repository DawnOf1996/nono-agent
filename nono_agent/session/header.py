"""SessionHeader —— 会话元信息与格式版本门。

事件流本身只追加事件；会话级元信息（会话身份、创建时间、工作目录、派生来源）
与事件流分离存放。``format_version`` 是兼容性闸门：版本不符直接拒读，
不做静默降级，也不尝试猜测旧格式。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, fields
from typing import Any

from nono_agent.paths import resolve_session_cwd
from nono_agent.session.events import (
    EVENT_USER_INPUT,
    EVENT_USER_MESSAGE,
    SESSION_FORMAT_VERSION,
)

# 会话列表用的短摘要：用户第一句话的前 N 个字符
SESSION_SUMMARY_MAX_CHARS = 20

_PERMISSION_MODES = frozenset({"default", "accept_edits", "plan", "bypass"})


def normalize_permission_mode(value: str | None) -> str:
    """把权限模式收成合法取值；未知/空则 ``default``。"""
    raw = (value or "").strip()
    return raw if raw in _PERMISSION_MODES else "default"


class VersionMismatchError(Exception):
    """会话日志无法按当前事件格式读取 —— 版本不符或根本不是事件流。"""


@dataclass
class SessionHeader:
    """会话元信息（与事件流分离存储）。

    ``parent_session`` 由 fork 写入，形成会话谱系；
    ``format_version`` 由写入方固定为写入时的 ``SESSION_FORMAT_VERSION``。
    """

    session_id: str = ""
    format_version: int = SESSION_FORMAT_VERSION
    created_at: float = 0.0
    cwd: str = ""
    parent_session: str | None = None
    agent_preset_id: str | None = None
    summary: str = ""  # 用户第一句话的前 SESSION_SUMMARY_MAX_CHARS 字
    permission_mode: str = "default"  # 最后一次 /permission（default/accept_edits/plan/bypass）

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 兼容字典。

        Returns:
            含全部字段的字典。
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionHeader:
        """从字典构造，忽略未知字段。

        Args:
            data: 至少含部分已知字段的字典。

        Returns:
            构造好的 SessionHeader。
        """
        known = {f.name for f in fields(cls)}
        payload = {k: v for k, v in data.items() if k in known}
        if payload.get("summary") is None:
            payload["summary"] = ""
        if payload.get("cwd") is None:
            payload["cwd"] = ""
        payload["permission_mode"] = normalize_permission_mode(
            payload.get("permission_mode")
        )
        return cls(**payload)


# 持久化后端共用的列顺序（SQLite 建表 / 读行对齐）
HEADER_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(SessionHeader))


def make_header(
    session_id: str,
    *,
    cwd: str = "",
    parent_session: str | None = None,
    agent_preset_id: str | None = None,
    summary: str = "",
    permission_mode: str = "default",
) -> SessionHeader:
    """构造带创建时间与当前格式版本的 SessionHeader。

    Args:
        session_id: 会话 ID。
        cwd: 会话工作目录（非空时收成绝对路径，禁止写入 ``.``）。
        parent_session: fork 来源会话 ID。
        agent_preset_id: Agent 预设标识。
        summary: 用户第一句话的短摘要（可空，首轮再写）。
        permission_mode: 会话权限模式（``/permission``）。

    Returns:
        新的 SessionHeader（``created_at`` 为毫秒时间戳）。
    """
    return SessionHeader(
        session_id=session_id,
        format_version=SESSION_FORMAT_VERSION,
        created_at=time.time() * 1000,
        cwd=resolve_session_cwd(cwd) if str(cwd).strip() else "",
        parent_session=parent_session,
        agent_preset_id=agent_preset_id,
        summary=summary,
        permission_mode=normalize_permission_mode(permission_mode),
    )


def summarize_user_prompt(
    text: str, *, max_chars: int = SESSION_SUMMARY_MAX_CHARS
) -> str:
    """取用户输入的前 ``max_chars`` 个字符作为会话摘要。"""
    return text.strip()[:max_chars]


def first_user_prompt(events: list[Any]) -> str:
    """从事件流取出用户第一句话（优先 ``user/input``，其次 ``user/message``）。"""
    for ev in events:
        if getattr(ev, "type", None) != EVENT_USER_INPUT:
            continue
        content = ev.data.get("content") if isinstance(ev.data, dict) else None
        if isinstance(content, str) and content.strip():
            return content
    for ev in events:
        if getattr(ev, "type", None) != EVENT_USER_MESSAGE:
            continue
        content = ev.data.get("content") if isinstance(ev.data, dict) else None
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    return ""


def check_format_version(header: SessionHeader | None) -> None:
    """版本门 —— 版本不符抛 VersionMismatchError。

    header 为 None 表示会话尚未写入元信息（新建会话），放行。

    Args:
        header: 待校验的会话元信息。

    Raises:
        VersionMismatchError: 版本与当前 SESSION_FORMAT_VERSION 不符。
    """
    if header is None:
        return
    if header.format_version != SESSION_FORMAT_VERSION:
        raise VersionMismatchError(
            f"session log format_version={header.format_version} "
            f"!= supported {SESSION_FORMAT_VERSION}"
        )


__all__ = [
    "HEADER_FIELDS",
    "SESSION_SUMMARY_MAX_CHARS",
    "SessionHeader",
    "VersionMismatchError",
    "check_format_version",
    "first_user_prompt",
    "make_header",
    "normalize_permission_mode",
    "summarize_user_prompt",
]
