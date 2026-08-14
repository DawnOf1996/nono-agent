"""SQLite Transcript 写入器 —— TranscriptWriter 协议的生产级后端起步。

每会话一个 SQLite 文件，``messages`` 表以自增 seq 保存 NDJSON 风格的
JSON 消息行。实现 record / record_append / read / flush 协议。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from nono_agent.core.types import Message
from nono_agent.session.transcript import serialize_message

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL
)
"""


class SQLiteTranscriptWriter:
    """SQLite 后端 Transcript 写入器。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute(_SCHEMA)
            self._conn.commit()
        return self._conn

    async def record(self, messages: list[Message]) -> None:
        """全量覆写：清空后写入所有消息。"""
        conn = self._connection()
        with self._lock:
            conn.execute("DELETE FROM messages")
            conn.executemany(
                "INSERT INTO messages (data) VALUES (?)",
                [(json.dumps(serialize_message(m), ensure_ascii=False),) for m in messages],
            )
            conn.commit()

    async def record_append(self, message: Message) -> None:
        """追加单条消息。"""
        conn = self._connection()
        with self._lock:
            conn.execute(
                "INSERT INTO messages (data) VALUES (?)",
                (json.dumps(serialize_message(message), ensure_ascii=False),),
            )
            conn.commit()

    async def read(self) -> list[dict[str, Any]]:
        """读取全部消息（按 seq 顺序），跳过损坏行。"""
        conn = self._connection()
        with self._lock:
            rows = conn.execute("SELECT data FROM messages ORDER BY seq").fetchall()

        messages: list[dict[str, Any]] = []
        for (data,) in rows:
            try:
                messages.append(json.loads(data))
            except json.JSONDecodeError:
                continue
        return messages

    async def read_range(self, start: int, end: int | None = None) -> list[dict[str, Any]]:
        """按 seq 范围读取消息（含 start，不含 end）。"""
        conn = self._connection()
        with self._lock:
            if end is None:
                rows = conn.execute(
                    "SELECT data FROM messages WHERE seq >= ? ORDER BY seq", (start,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM messages WHERE seq >= ? AND seq < ? ORDER BY seq",
                    (start, end),
                ).fetchall()
        return [json.loads(data) for (data,) in rows]

    async def flush(self) -> None:
        """强制提交。"""
        if self._conn is not None:
            with self._lock:
                self._conn.commit()

    async def delete(self) -> None:
        """删除会话数据（清空表）。"""
        conn = self._connection()
        with self._lock:
            conn.execute("DELETE FROM messages")
            conn.commit()

    def close(self) -> None:
        """关闭底层连接。"""
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None
