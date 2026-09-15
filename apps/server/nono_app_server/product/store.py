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
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace
    ON sessions(workspace_id, updated_at DESC);
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
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

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
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM workspaces ORDER BY updated_at DESC"
            ).fetchall()
        return [self._workspace_dict(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
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
                    "UPDATE workspaces SET path = ?, name = ?, updated_at = ? WHERE id = ?",
                    (path, name, now, existing["id"]),
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
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            return self._workspace_dict(row)

    def delete_workspace(self, workspace_id: str) -> bool:
        with self._tx() as conn:
            cursor = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            return cursor.rowcount > 0

    def list_sessions(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT s.*, w.path AS cwd
            FROM sessions s
            JOIN workspaces w ON w.id = s.workspace_id
        """
        params: tuple[str, ...] = ()
        if workspace_id:
            sql += " WHERE s.workspace_id = ?"
            params = (workspace_id,)
        sql += " ORDER BY s.updated_at DESC"
        with self._tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._session_dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT s.*, w.path AS cwd
                FROM sessions s
                JOIN workspaces w ON w.id = s.workspace_id
                WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._session_dict(row) if row else None

    def get_session_by_engine_id(self, engine_session_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT s.*, w.path AS cwd
                FROM sessions s
                JOIN workspaces w ON w.id = s.workspace_id
                WHERE s.engine_session_id = ?
                """,
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
                "SELECT id FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            if workspace is None:
                raise KeyError(f"workspace not found: {workspace_id}")
            conn.execute(
                """
                INSERT INTO sessions (
                    id, engine_session_id, workspace_id, title, model,
                    permission_mode, turn_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    engine_session_id,
                    workspace_id,
                    title,
                    model,
                    permission_mode,
                    turn_count,
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
    ) -> dict[str, Any] | None:
        current = self.get_session(session_id)
        if current is None:
            return None
        next_title = current["title"] if title is None else title
        next_model = current["model"] if model is None else model
        next_mode = current["permission_mode"] if permission_mode is None else permission_mode
        next_turns = current["turn_count"] if turn_count is None else turn_count
        now = _now()
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET title = ?, model = ?, permission_mode = ?, turn_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_title, next_model, next_mode, next_turns, now, session_id),
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
