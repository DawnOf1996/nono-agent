"""产品层 SQLite：工作区与会话列表映射（不含会话详情 / transcript）。"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from nono_agent.paths import resolve_session_cwd, user_home
from nono_app_server.config import ENV_APP_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    engine_session_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    permission_mode TEXT NOT NULL DEFAULT 'default',
    turn_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    side_of TEXT,
    cwd TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace
    ON sessions(workspace_id, updated_at DESC);
"""

UNGROUPED_PATH_KEY = "__nono_ungrouped__"
UNGROUPED_NAME = "未分组"

_SESSION_SELECT = """
SELECT
    s.id,
    s.engine_session_id,
    s.workspace_id,
    s.title,
    s.model,
    s.permission_mode,
    s.turn_count,
    s.archived,
    s.side_of,
    s.created_at,
    s.updated_at,
    COALESCE(NULLIF(s.cwd, ''), w.path) AS cwd
FROM sessions s
JOIN workspaces w ON w.id = s.workspace_id
"""


def default_db_path() -> Path:
    override = os.environ.get(ENV_APP_DB, "").strip()
    if override:
        return Path(override).expanduser()
    return user_home() / "app.db"


def path_key(cwd: str) -> str:
    normalized = resolve_session_cwd(cwd)
    key = normalized.replace("/", "\\") if os.name == "nt" else normalized
    key = key.rstrip("\\/")
    return key.lower() if os.name == "nt" else key


def workspace_name(cwd: str) -> str:
    return Path(cwd).name or cwd


def _now() -> float:
    return time.time()


class AppStore:
    """线程安全的 sidecar 前端数据存储。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "archived" not in columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        if "cwd" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN cwd TEXT NOT NULL DEFAULT ''")
        if "side_of" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN side_of TEXT")
            self._conn.execute(
                """
                UPDATE sessions
                SET cwd = COALESCE(
                    (SELECT path FROM workspaces WHERE id = sessions.workspace_id),
                    ''
                )
                WHERE cwd = ''
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def checkpoint(self) -> None:
        """把 WAL 里还没并进主库的事务落盘，让 ``app.db`` 自己就是完整的库。

        搬数据目录之前要调：WAL 模式下建表和写入都先进 ``-wal``，主库可能还是空的。
        整目录拷贝会连 ``-wal``/``-shm`` 一起带走，所以当场看不出问题；但那份
        ``app.db`` 脱离伴生文件就打不开（表根本不存在），备份、单独拷库、任何
        「只留 app.db」的场景都会拿到空数据。
        """
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _workspace_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "path": row["path"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _session_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "engine_session_id": row["engine_session_id"],
            "workspace_id": row["workspace_id"],
            "cwd": row["cwd"],
            "title": row["title"],
            "model": row["model"],
            "permission_mode": row["permission_mode"],
            "turn_count": int(row["turn_count"]),
            "archived": bool(row["archived"]) if "archived" in row.keys() else False,
            "side_of": row["side_of"] if "side_of" in row.keys() else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workspaces
                WHERE path_key != ?
                ORDER BY updated_at DESC
                """,
                (UNGROUPED_PATH_KEY,),
            ).fetchall()
        return [self._workspace_dict(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return self._workspace_dict(row) if row else None

    def upsert_workspace(self, cwd: str) -> dict[str, Any]:
        path = resolve_session_cwd(cwd)
        key = path_key(path)
        name = workspace_name(path)
        now = _now()
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM workspaces WHERE path_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE workspaces SET path = ?, updated_at = ? WHERE id = ?",
                    (path, now, existing["id"]),
                )
                row = conn.execute(
                    "SELECT * FROM workspaces WHERE id = ?", (existing["id"],)
                ).fetchone()
                return self._workspace_dict(row)
            workspace_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO workspaces (id, path, path_key, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (workspace_id, path, key, name, now, now),
            )
            row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            return self._workspace_dict(row)

    def update_workspace(self, workspace_id: str, *, name: str) -> dict[str, Any] | None:
        current = self.get_workspace(workspace_id)
        if current is None:
            return None
        next_name = name.strip()
        if not next_name:
            return current
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?",
                (next_name, now, workspace_id),
            )
        return self.get_workspace(workspace_id)

    def _ensure_ungrouped_id(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE path_key = ?",
            (UNGROUPED_PATH_KEY,),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        workspace_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """
            INSERT INTO workspaces (id, path, path_key, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, "", UNGROUPED_PATH_KEY, UNGROUPED_NAME, now, now),
        )
        return workspace_id

    def delete_workspace(self, workspace_id: str) -> bool:
        with self._tx() as conn:
            current = conn.execute(
                "SELECT path_key FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            if current is None:
                return False
            if current["path_key"] == UNGROUPED_PATH_KEY:
                raise ValueError("ungrouped workspace cannot be deleted")
            ungrouped_id = self._ensure_ungrouped_id(conn)
            conn.execute(
                "UPDATE sessions SET workspace_id = ? WHERE workspace_id = ?",
                (ungrouped_id, workspace_id),
            )
            cursor = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            return cursor.rowcount > 0

    def list_sessions(
        self,
        workspace_id: str | None = None,
        *,
        side_of: str | None = None,
        archived: str = "hide",
    ) -> list[dict[str, Any]]:
        if archived not in {"hide", "all", "only"}:
            raise ValueError("archived must be hide, all, or only")
        params: list[str] = []
        clauses: list[str] = []
        if side_of:
            clauses.append("s.side_of = ?")
            params.append(side_of)
        else:
            clauses.append("(s.side_of IS NULL OR s.side_of = '')")
        if archived == "hide":
            clauses.append("s.archived = 0")
        elif archived == "only":
            clauses.append("s.archived = 1")
        if workspace_id:
            clauses.append("s.workspace_id = ?")
            params.append(workspace_id)
        sql = _SESSION_SELECT + " WHERE " + " AND ".join(clauses) + " ORDER BY s.updated_at DESC"
        with self._tx() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._session_dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                _SESSION_SELECT + " WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        return self._session_dict(row) if row else None

    def get_session_by_engine_id(self, engine_session_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                _SESSION_SELECT + " WHERE s.engine_session_id = ?",
                (engine_session_id,),
            ).fetchone()
        return self._session_dict(row) if row else None

    def create_session(
        self,
        *,
        workspace_id: str,
        engine_session_id: str,
        title: str = "",
        model: str = "",
        permission_mode: str = "default",
        turn_count: int = 0,
        cwd: str | None = None,
        session_id: str | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> dict[str, Any]:
        now = _now()
        product_id = session_id or str(uuid.uuid4())
        stamp_created = created_at if created_at is not None else now
        stamp_updated = updated_at if updated_at is not None else now
        with self._tx() as conn:
            workspace = conn.execute(
                "SELECT id, path, path_key FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            if workspace is None:
                raise KeyError(f"workspace not found: {workspace_id}")
            session_cwd = (cwd or "").strip() or str(workspace["path"] or "")
            if workspace["path_key"] == UNGROUPED_PATH_KEY and not session_cwd:
                raise ValueError("cannot create session in ungrouped workspace")
            conn.execute(
                """
                INSERT INTO sessions (
                    id, engine_session_id, workspace_id, title, model,
                    permission_mode, turn_count, cwd, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    engine_session_id,
                    workspace_id,
                    title,
                    model,
                    permission_mode,
                    turn_count,
                    session_cwd,
                    stamp_created,
                    stamp_updated,
                ),
            )
        row = self.get_session(product_id)
        assert row is not None
        return row

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        turn_count: int | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_session(session_id)
        if current is None:
            return None
        next_title = current["title"] if title is None else title
        next_model = current["model"] if model is None else model
        next_mode = current["permission_mode"] if permission_mode is None else permission_mode
        next_turns = current["turn_count"] if turn_count is None else turn_count
        next_archived = int(current["archived"]) if archived is None else int(archived)
        now = _now()
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET title = ?, model = ?, permission_mode = ?, turn_count = ?,
                    archived = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_title, next_model, next_mode, next_turns, next_archived, now, session_id),
            )
        return self.get_session(session_id)

    def set_side_of(self, session_id: str, parent_id: str | None) -> dict[str, Any] | None:
        """挂到主会话下面，或传空把它提升成顶层会话。"""
        if self.get_session(session_id) is None:
            return None
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET side_of = ?, updated_at = ? WHERE id = ?",
                (parent_id or None, _now(), session_id),
            )
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> dict[str, Any] | None:
        current = self.get_session(session_id)
        if current is None:
            return None
        with self._tx() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return current

    def engine_ids_for_workspace(self, workspace_id: str) -> list[str]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT engine_session_id FROM sessions WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        return [str(row["engine_session_id"]) for row in rows]

    def has_imported_engine_index(self) -> bool:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'engine_index_imported'"
            ).fetchone()
        return bool(row and row["value"] == "1")

    def mark_engine_index_imported(self) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('engine_index_imported', '1')"
            )

    def import_engine_index(self, items: list[dict[str, Any]]) -> int:
        """把引擎磁盘会话索引导入列表库，只应执行一次。"""
        imported = 0
        for item in items:
            engine_id = str(item.get("session_id") or "").strip()
            cwd = str(item.get("cwd") or "").strip()
            if not engine_id or not cwd:
                continue
            if self.get_session_by_engine_id(engine_id) is not None:
                imported += 1
                continue
            workspace = self.upsert_workspace(cwd)
            self.create_session(
                workspace_id=workspace["id"],
                engine_session_id=engine_id,
                title=str(item.get("summary") or ""),
                permission_mode=str(item.get("permission_mode") or "default"),
                turn_count=int(item.get("turn_count") or 0),
                updated_at=float(item.get("last_modified") or _now()),
            )
            imported += 1
        self.mark_engine_index_imported()
        return imported
