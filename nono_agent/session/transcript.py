"""会话事件流持久化 —— 抽象协议 + 本地文件实现（默认后端）。

架构设计：

- ``TranscriptWriter``：Protocol，定义**事件流**读写接口。落盘内容只有
  ``SessionEvent``；消息数组不落盘，由 ``derive_messages()`` 从事件推导。
- ``LocalFileTranscriptWriter``：``<sid>.jsonl`` 每行一个事件（append-only），
  会话元信息写在同目录的 ``<sid>.header.json``。
- 规划扩展：PostgreSQL / MySQL、Redis、S3 等远端/共享后端（实现同一协议后注入
  QueryEngine；详见 docs/ROADMAP.md §2.5）。

事件只追加不改写：历史不可变，「修正」用新事件表达（compact 边界、tombstone），
因此没有全量覆写接口 —— 覆写会与追加交错并破坏事件流。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import aiofiles  # type: ignore[import-untyped]

from nono_agent.session.events import SessionEvent
from nono_agent.session.header import (
    SessionHeader,
    VersionMismatchError,
    check_format_version,
)

logger = logging.getLogger(__name__)

# 默认会话文件存储目录
DEFAULT_SESSION_DIR = Path.home() / ".nono-agent" / "sessions"

# 会话元信息侧车文件后缀（与 <sid>.jsonl 同目录）
HEADER_SUFFIX = ".header.json"

# 会话文件权限：仅所有者可读写。
# 事件流逐字保留提示词、工具输出与请求配置（脱敏在读取侧，见 session/redact.py），
# 因此落盘时就必须挡住同机其它用户。
SESSION_FILE_MODE = 0o600

# 会话目录权限：仅所有者可进入 —— 否则会话 ID 列表本身就是可枚举的。
SESSION_DIR_MODE = 0o700


# ------------------------------------------------------------------
# 共享工具函数
# ------------------------------------------------------------------

def list_session_files(session_dir: Path | None = None) -> list[dict[str, Any]]:
    """列出会话目录下的事件流文件（id + 事件数 + 最后修改时间）。

    用于 CLI ``sessions`` 命令 / Server 会话治理。

    Args:
        session_dir: 会话目录；None 时用 ``DEFAULT_SESSION_DIR``。

    Returns:
        每个会话一项的字典列表，按 session_id 排序。
    """
    directory = Path(session_dir) if session_dir is not None else DEFAULT_SESSION_DIR
    if not directory.is_dir():
        return []

    sessions: list[dict[str, Any]] = []
    for file in sorted(directory.glob("*.jsonl")):
        try:
            line_count = sum(1 for line in file.open(encoding="utf-8") if line.strip())
        except OSError:
            line_count = 0
        sessions.append(
            {
                "session_id": file.stem,
                "event_count": line_count,
                "last_modified": file.stat().st_mtime,
            }
        )
    return sessions


def parse_event_line(line: str) -> SessionEvent | None:
    """解析事件流中的一行。

    Args:
        line: JSONL 中的一行（已去除首尾空白）。

    Returns:
        解析出的事件；该行不是合法 JSON 对象（写入中断产生的残行）时返回 None。

    Raises:
        VersionMismatchError: 该行是合法 JSON 对象但缺少事件信封字段，
            说明文件是事件格式之前的消息副本 transcript。
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "seq" not in obj or "data" not in obj:
        raise VersionMismatchError(
            "transcript line is not a SessionEvent (legacy message-copy transcript); "
            "event-sourced sessions cannot be read from it"
        )
    return SessionEvent.model_validate(obj)


def restrict_permissions(path: Path) -> None:
    """把会话文件及其所在目录收紧到仅所有者可访问。

    每次写入后调用（而不是只在创建时）：目录可能是既有的宽权限目录，
    文件也可能由更早的版本创建。``chmod`` 在不支持 POSIX 权限位的平台上
    是无操作，失败仅代表无法收紧，不影响会话写入本身。

    Args:
        path: 刚写入的会话文件路径。
    """
    try:
        path.parent.chmod(SESSION_DIR_MODE)
        path.chmod(SESSION_FILE_MODE)
    except OSError:
        # 只读挂载、网络文件系统或 Windows ACL 下无法设置权限位；
        # 这不影响事件写入，也没有更强的收紧手段可退回。
        logger.debug("Cannot restrict permissions on %s", path)


# ------------------------------------------------------------------
# Protocol 协议类
# ------------------------------------------------------------------

@runtime_checkable
class TranscriptWriter(Protocol):
    """会话事件流持久化协议。

    所有存储后端（文件、数据库、Redis、对象存储等）都实现此协议。
    CLI 或 Server 可在创建 QueryEngine 时显式指定实现：

    用法示例::

        # 默认使用本地文件事件流
        engine = QueryEngine(config)

        # 显式指定其它后端
        engine = QueryEngine(config, transcript_writer=SQLiteTranscriptWriter(path))
    """

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        """追加一条事件。

        Args:
            event: 待写入事件；``seq`` 由后端分配，调用方传入的值被忽略。

        Returns:
            带已分配 ``seq`` 的同一事件对象。
        """
        ...

    async def read_events(self) -> list[SessionEvent]:
        """读取全部事件（按 seq 升序）。

        Returns:
            事件列表。

        Raises:
            VersionMismatchError: 会话格式版本不符，或文件不是事件流。
        """
        ...

    async def read_events_from(self, start_seq: int) -> list[SessionEvent]:
        """读取 ``seq >= start_seq`` 的事件。

        Args:
            start_seq: 起始序号（含）。

        Returns:
            事件列表（按 seq 升序）。
        """
        ...

    async def write_header(self, header: SessionHeader) -> None:
        """写入（或覆盖）会话元信息。

        Args:
            header: 会话元信息。
        """
        ...

    async def read_header(self) -> SessionHeader | None:
        """读取会话元信息。

        Returns:
            会话元信息；尚未写入时为 None。
        """
        ...

    async def flush(self) -> None:
        """确保已提交的写入落盘。"""
        ...


# ------------------------------------------------------------------
# 本地文件实现
# ------------------------------------------------------------------

class LocalFileTranscriptWriter:
    """本地 JSONL 事件流写入器（默认存储后端）。

    每行一个 ``SessionEvent``，纯追加；元信息写在 ``<sid>.header.json``。
    每次追加都开闭文件，进程在轮次之间被杀不会丢已写事件。
    """

    def __init__(self, session_file: Path) -> None:
        """初始化本地事件流写入器。

        Args:
            session_file: 事件流 JSONL 文件路径（``<sid>.jsonl``）。
        """
        self._session_file = Path(session_file)
        self._write_lock = asyncio.Lock()  # 防止并发写入交错
        self._next_seq: int | None = None  # 惰性从既有文件恢复，避免重启后 seq 冲突

    @property
    def session_file(self) -> Path:
        """事件流文件路径。"""
        return self._session_file

    @property
    def header_file(self) -> Path:
        """会话元信息侧车文件路径。"""
        return self._session_file.parent / (self._session_file.stem + HEADER_SUFFIX)

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        """追加一条事件；seq 在既有文件最大值之后继续分配。"""
        async with self._write_lock:
            if self._next_seq is None:
                self._next_seq = self._scan_max_seq() + 1
            event.seq = self._next_seq
            self._next_seq += 1

            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._session_file, "a", encoding="utf-8") as f:
                await f.write(event.model_dump_json() + "\n")
            restrict_permissions(self._session_file)
        return event

    async def read_events(self) -> list[SessionEvent]:
        """读取全部事件（按 seq 升序），跳过写入中断产生的残行。"""
        check_format_version(await self.read_header())
        if not self._session_file.exists():
            return []

        events: list[SessionEvent] = []
        async with self._write_lock:
            async with aiofiles.open(self._session_file, encoding="utf-8") as f:
                async for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    event = parse_event_line(line)
                    if event is not None:
                        events.append(event)
        events.sort(key=lambda e: e.seq)
        return events

    async def read_events_from(self, start_seq: int) -> list[SessionEvent]:
        """读取 ``seq >= start_seq`` 的事件。"""
        return [e for e in await self.read_events() if e.seq >= start_seq]

    async def write_header(self, header: SessionHeader) -> None:
        """把会话元信息写入侧车文件。"""
        async with self._write_lock:
            self.header_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.header_file, "w", encoding="utf-8") as f:
                await f.write(
                    json.dumps(header.to_dict(), ensure_ascii=False, indent=2) + "\n"
                )
            restrict_permissions(self.header_file)

    async def read_header(self) -> SessionHeader | None:
        """读取侧车文件中的会话元信息。"""
        if not self.header_file.exists():
            return None
        async with aiofiles.open(self.header_file, encoding="utf-8") as f:
            text = await f.read()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VersionMismatchError(
                f"session header {self.header_file} is not valid JSON"
            ) from exc
        return SessionHeader.from_dict(data)

    async def flush(self) -> None:
        """等待在途写入结束（每次追加已开闭文件，无额外缓冲）。"""
        async with self._write_lock:
            pass

    def _scan_max_seq(self) -> int:
        """扫描既有文件的最大 seq；文件不存在或为空返回 0。"""
        if not self._session_file.exists():
            return 0
        max_seq = 0
        with self._session_file.open(encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                event = parse_event_line(line)
                if event is not None and event.seq > max_seq:
                    max_seq = event.seq
        return max_seq


__all__ = [
    "DEFAULT_SESSION_DIR",
    "HEADER_SUFFIX",
    "SESSION_DIR_MODE",
    "SESSION_FILE_MODE",
    "LocalFileTranscriptWriter",
    "TranscriptWriter",
    "list_session_files",
    "parse_event_line",
    "restrict_permissions",
]
