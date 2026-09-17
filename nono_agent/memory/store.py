"""MemoryStore —— 跨会话记忆存储协议 + 文件系统 / SQLite 实现。

把「文件名即记忆」升级为「可治理的记忆记录」：每条记忆是一个带完整
envelope 的 JSON 对象，存储后端实现统一协议（load / add / update / delete /
get / list_candidates / list_all）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nono_agent.memory.models import (
    STATUS_ACCEPTED,
    STATUS_CANDIDATE,
    STATUS_RETIRED,
    MemoryRecord,
)
from nono_agent.paths import user_home

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
)
"""

# 默认记忆目录 —— 与 config / sessions 同属用户运行时根目录
DEFAULT_MEMORY_DIR = user_home() / "memory"


@runtime_checkable
class MemoryStore(Protocol):
    """跨会话记忆存储协议 —— 所有后端都必须实现。"""

    def load(self) -> list[MemoryRecord]:
        """加载全部记忆（含候选/已接受；调用方自行过滤）。"""
        ...

    def add(self, record: MemoryRecord) -> MemoryRecord:
        """新增或覆写一条记忆（返回补全 identity 后的记录）。"""
        ...

    def update(self, memory_id: str, **patch: Any) -> MemoryRecord | None:
        """按字段打补丁更新（版本化更新，不静默覆写正文）。"""
        ...

    def delete(self, memory_id: str) -> bool:
        """硬删除一条记忆。"""
        ...

    def get(self, memory_id: str) -> MemoryRecord | None:
        """按 id 读取一条记忆。"""
        ...

    def list_candidates(self) -> list[MemoryRecord]:
        """列出候选池（status=candidate）。"""
        ...

    def list_all(self) -> list[MemoryRecord]:
        """列出全部（含 retired）。"""
        ...


class FileSystemMemoryStore:
    """文件系统记忆存储 —— 每条记忆一个 ``.json`` 文件。

    默认目录是 ``~/.nono-agent/memory``，与用户配置和会话日志同根。
    与早期 memdir ``.md`` 约定不兼容；旧 ``.md`` 文件会被 ``load()`` 忽略。
    """

    def __init__(self, memdir: str | Path | None = None) -> None:
        """初始化文件系统记忆存储。

        Args:
            memdir: 记忆目录；None 时使用 ``DEFAULT_MEMORY_DIR``。
        """
        self._memdir = Path(memdir) if memdir is not None else DEFAULT_MEMORY_DIR

    @property
    def memdir(self) -> Path:
        return self._memdir

    # ------------------------------------------------------------------
    # MemoryStore 协议
    # ------------------------------------------------------------------

    def load(self) -> list[MemoryRecord]:
        memories = [m for m in self._iter_files()]
        memories.sort(key=lambda m: m.created_at, reverse=True)
        return memories

    def add(self, record: MemoryRecord) -> MemoryRecord:
        record.ensure_identity()
        self._memdir.mkdir(parents=True, exist_ok=True)
        file = self._memdir / f"{record.memory_id}.json"
        file.write_text(json.dumps(record.to_dict(), ensure_ascii=False), encoding="utf-8")
        return record

    def update(self, memory_id: str, **patch: Any) -> MemoryRecord | None:
        existing = self.get(memory_id)
        if existing is None:
            return None
        patch.pop("memory_id", None)  # 不允许改主键
        data = existing.to_dict()
        data.update(patch)
        updated = MemoryRecord.from_dict(data)
        file = self._memdir / f"{memory_id}.json"
        file.write_text(json.dumps(updated.to_dict(), ensure_ascii=False), encoding="utf-8")
        return updated

    def delete(self, memory_id: str) -> bool:
        file = self._memdir / f"{memory_id}.json"
        if file.exists():
            file.unlink()
            return True
        return False

    def get(self, memory_id: str) -> MemoryRecord | None:
        file = self._memdir / f"{memory_id}.json"
        if not file.exists():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return None
        return MemoryRecord.from_dict(data)

    def list_candidates(self) -> list[MemoryRecord]:
        return [m for m in self.load() if m.status == STATUS_CANDIDATE]

    def list_all(self) -> list[MemoryRecord]:
        return self.load()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _iter_files(self) -> list[MemoryRecord]:
        if not self._memdir.is_dir():
            return []
        memories: list[MemoryRecord] = []
        for file in sorted(self._memdir.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or not data.get("content"):
                continue
            memories.append(MemoryRecord.from_dict(data))
        return memories


class SQLiteMemoryStore:
    """SQLite 记忆存储 —— 单文件，含全文检索与结构化查询能力。

    ``memories(memory_id, data)`` 表以 JSON 存完整 envelope；查询在 Python
    层完成（数据量级为单机几十 NPC，足够），后续可加 FTS5 虚拟表。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
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

    # ------------------------------------------------------------------
    # MemoryStore 协议
    # ------------------------------------------------------------------

    def load(self) -> list[MemoryRecord]:
        conn = self._connection()
        with self._lock:
            rows = conn.execute("SELECT data FROM memories").fetchall()
        memories: list[MemoryRecord] = []
        for row in rows:
            m = self._parse(row[0])
            if m is not None:
                memories.append(m)
        memories.sort(key=lambda m: m.created_at, reverse=True)
        return memories

    def add(self, record: MemoryRecord) -> MemoryRecord:
        record.ensure_identity()
        conn = self._connection()
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO memories (memory_id, data) VALUES (?, ?)",
                (record.memory_id, json.dumps(record.to_dict(), ensure_ascii=False)),
            )
            conn.commit()
        return record

    def update(self, memory_id: str, **patch: Any) -> MemoryRecord | None:
        existing = self.get(memory_id)
        if existing is None:
            return None
        patch.pop("memory_id", None)
        data = existing.to_dict()
        data.update(patch)
        updated = MemoryRecord.from_dict(data)
        conn = self._connection()
        with self._lock:
            conn.execute(
                "UPDATE memories SET data = ? WHERE memory_id = ?",
                (json.dumps(updated.to_dict(), ensure_ascii=False), memory_id),
            )
            conn.commit()
        return updated

    def delete(self, memory_id: str) -> bool:
        conn = self._connection()
        with self._lock:
            cur = conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            conn.commit()
        return cur.rowcount > 0

    def get(self, memory_id: str) -> MemoryRecord | None:
        conn = self._connection()
        with self._lock:
            row = conn.execute(
                "SELECT data FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        if row is None:
            return None
        return self._parse(row[0])

    def list_candidates(self) -> list[MemoryRecord]:
        return [m for m in self.load() if m.status == STATUS_CANDIDATE]

    def list_all(self) -> list[MemoryRecord]:
        return self.load()

    def retire(self, memory_id: str) -> bool:
        """软删：置 retired（保留记录与版本链）。"""
        return self.update(memory_id, status=STATUS_RETIRED) is not None

    def accept(self, memory_id: str) -> bool:
        """把候选记忆转为已接受。"""
        return self.update(memory_id, status=STATUS_ACCEPTED) is not None

    def close(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(data: str) -> MemoryRecord | None:
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        return MemoryRecord.from_dict(obj)
