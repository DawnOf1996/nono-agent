"""SessionHeader —— 会话元信息与格式版本门。

事件流本身只追加事件；会话级元信息（会话身份、创建时间、工作目录、派生来源）
与事件流分离存放。``format_version`` 是兼容性闸门：版本不符直接拒读，
不做静默降级，也不尝试猜测旧格式。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, fields
from typing import Any

from nono_agent.session.events import SESSION_FORMAT_VERSION


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
        return cls(**{k: v for k, v in data.items() if k in known})


# 持久化后端共用的列顺序（SQLite 建表 / 读行对齐）
HEADER_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(SessionHeader))


def make_header(
    session_id: str,
    *,
    cwd: str = "",
    parent_session: str | None = None,
    agent_preset_id: str | None = None,
) -> SessionHeader:
    """构造带创建时间与当前格式版本的 SessionHeader。

    Args:
        session_id: 会话 ID。
        cwd: 会话工作目录。
        parent_session: fork 来源会话 ID。
        agent_preset_id: Agent 预设标识。

    Returns:
        新的 SessionHeader（``created_at`` 为毫秒时间戳）。
    """
    return SessionHeader(
        session_id=session_id,
        format_version=SESSION_FORMAT_VERSION,
        created_at=time.time() * 1000,
        cwd=cwd,
        parent_session=parent_session,
        agent_preset_id=agent_preset_id,
    )


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
    "SessionHeader",
    "VersionMismatchError",
    "check_format_version",
    "make_header",
]
