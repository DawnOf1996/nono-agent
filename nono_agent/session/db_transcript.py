"""SQLite 事件流写入器 —— TranscriptWriter 协议的生产级后端起步。

每会话一个 SQLite 文件：``events`` 表以自增 seq 保存事件信封，结构化列
（``type`` / ``timestamp``）可建索引，支撑按类型/时间的回溯查询；
``session_header`` 表单独保存会话元信息与格式版本。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nono_agent.session.events import SessionEvent
from nono_agent.session.header import (
    HEADER_FIELDS,
    SessionHeader,
    VersionMismatchError,
    check_format_version,
)
from nono_agent.session.transcript import restrict_permissions

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    timestamp REAL NOT NULL,
    ignorable INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (type);
CREATE TABLE IF NOT EXISTS session_header (
    session_id TEXT PRIMARY KEY,
    format_version INTEGER NOT NULL,
    created_at REAL,
    cwd TEXT,
    parent_session TEXT,
    agent_preset_id TEXT,
    summary TEXT,
    permission_mode TEXT
)
"""

_HEADER_COLUMN_TYPES: dict[str, str] = {
    "session_id": "TEXT PRIMARY KEY",
    "format_version": "INTEGER NOT NULL",
    "created_at": "REAL",
    "cwd": "TEXT",
    "parent_session": "TEXT",
    "agent_preset_id": "TEXT",
    "summary": "TEXT",
    "permission_mode": "TEXT",
}

_SELECT_COLUMNS = "seq, type, data, timestamp, ignorable"


class SQLiteTranscriptWriter:
    """SQLite 后端事件流写入器。"""

    def __init__(self, db_path: Path) -> None:
        """初始化 SQLite 事件流写入器。

        Args:
            db_path: 会话数据库文件路径。
        """
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        """会话数据库文件路径。"""
        return self._db_path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.executescript(_SCHEMA)
            self._migrate_header_columns(self._conn)
            self._conn.commit()
            restrict_permissions(self._db_path)
        return self._conn

    def _has_backing_store(self) -> bool:
        """尚未 write/append 时不要创建空库文件。"""
        return self._conn is not None or self._db_path.is_file()

    # ------------------------------------------------------------------
    # 事件追加 / 读取
    # ------------------------------------------------------------------

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        """追加一条事件；seq 由数据库自增分配。"""
        written = await self.append_events([event])
        return written[0]

    async def append_events(self, events: Sequence[SessionEvent]) -> list[SessionEvent]:
        """按序追加一批事件，同一事务提交。"""
        if not events:
            return []
        conn = self._connection()
        assigned: list[SessionEvent] = []
        with self._lock:
            for event in events:
                cur = conn.execute(
                    "INSERT INTO events (type, data, timestamp, ignorable) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        event.type,
                        json.dumps(event.data, ensure_ascii=False),
                        event.timestamp,
                        1 if event.ignorable else 0,
                    ),
                )
                if cur.lastrowid is not None:
                    event.seq = cur.lastrowid
                assigned.append(event)
            conn.commit()
        return assigned

    async def read_events(self) -> list[SessionEvent]:
        """读取全部事件（按 seq 升序），跳过 data 列损坏的行。"""
        if not self._has_backing_store():
            return []
        check_format_version(await self.read_header())
        conn = self._connection()
        with self._lock:
            self._reject_legacy(conn)
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM events ORDER BY seq"
            ).fetchall()
        return self._parse_rows(rows)

    async def read_events_from(self, start_seq: int) -> list[SessionEvent]:
        """读取 ``seq >= start_seq`` 的事件。"""
        if not self._has_backing_store():
            return []
        check_format_version(await self.read_header())
        conn = self._connection()
        with self._lock:
            self._reject_legacy(conn)
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM events WHERE seq >= ? ORDER BY seq",
                (start_seq,),
            ).fetchall()
        return self._parse_rows(rows)

    # ------------------------------------------------------------------
    # SessionHeader
    # ------------------------------------------------------------------

    async def write_header(self, header: SessionHeader) -> None:
        """写入（或覆盖）会话元信息。"""
        conn = self._connection()
        columns = ", ".join(HEADER_FIELDS)
        placeholders = ", ".join("?" for _ in HEADER_FIELDS)
        values = tuple(getattr(header, name) for name in HEADER_FIELDS)
        with self._lock:
            conn.execute("DELETE FROM session_header")
            conn.execute(
                f"INSERT INTO session_header ({columns}) VALUES ({placeholders})",
                values,
            )
            conn.commit()

    async def read_header(self) -> SessionHeader | None:
        """读取会话元信息。"""
        if not self._has_backing_store():
            return None
        conn = self._connection()
        columns = ", ".join(HEADER_FIELDS)
        with self._lock:
            row = conn.execute(f"SELECT {columns} FROM session_header").fetchone()
        if row is None:
            return None
        return SessionHeader.from_dict(dict(zip(HEADER_FIELDS, row, strict=True)))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """提交在途事务。"""
        if self._conn is not None:
            with self._lock:
                self._conn.commit()

    async def delete(self) -> None:
        """删除会话数据（事件与元信息）。"""
        conn = self._connection()
        with self._lock:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM session_header")
            conn.commit()

    def close(self) -> None:
        """关闭底层连接。"""
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _migrate_header_columns(conn: sqlite3.Connection) -> None:
        """为既有库补上新增的 session_header 列（如 summary）。"""
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(session_header)").fetchall()
        }
        for name in HEADER_FIELDS:
            if name in existing:
                continue
            coltype = _HEADER_COLUMN_TYPES.get(name, "TEXT")
            # ALTER TABLE 不能带 PRIMARY KEY；只给后加列用简单类型
            simple = coltype.split()[0]
            conn.execute(f"ALTER TABLE session_header ADD COLUMN {name} {simple}")

    @staticmethod
    def _reject_legacy(conn: sqlite3.Connection) -> None:
        """事件格式之前的库以 ``messages`` 表存消息副本 —— 拒读而非当作空会话。"""
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if row is not None:
            raise VersionMismatchError(
                "session database has a legacy 'messages' table "
                "(message-copy transcript); event-sourced sessions cannot be read from it"
            )

    @staticmethod
    def _parse_rows(rows: list[tuple[Any, ...]]) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        for seq, type_, data, timestamp, ignorable in rows:
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            events.append(
                SessionEvent(
                    seq=seq,
                    type=type_,
                    data=payload,
                    timestamp=timestamp,
                    ignorable=bool(ignorable),
                )
            )
        return events


__all__ = ["SQLiteTranscriptWriter"]
