"""把 QueryEngine / 磁盘会话投影为 API 的 SessionInfo。"""

from __future__ import annotations

import time
from typing import Any

from nono_agent.core.engine import QueryEngine
from nono_agent.permissions.policy import PermissionMode
from nono_agent.session.header import normalize_permission_mode
from nono_agent.session.transcript import peek_session_header
from nono_app_server.engine.schemas import SessionInfo


def effective_permission_mode(engine: QueryEngine) -> str:
    pending = engine.permission_policy.pending_mode
    return (pending or engine.permission_policy.mode).value


def request_permission_mode(engine: QueryEngine, raw: str | None) -> str:
    mode = PermissionMode(normalize_permission_mode(raw))
    engine.permission_policy.request_mode(mode)
    return effective_permission_mode(engine)


def session_summary(engine: QueryEngine) -> str:
    header = engine._session_header
    if header is not None and header.summary:
        return header.summary
    peeked = peek_session_header(engine._config.session_id)
    return peeked.summary if peeked and peeked.summary else ""


def session_updated_at(engine: QueryEngine) -> float:
    writer = engine.transcript_writer
    path = getattr(writer, "session_file", None) if writer is not None else None
    if path is not None:
        try:
            return float(path.stat().st_mtime)
        except OSError:
            pass
    header = engine._session_header
    if header is not None and header.created_at:
        return header.created_at / 1000.0
    return time.time()


def session_info(engine: QueryEngine) -> SessionInfo:
    return SessionInfo(
        session_id=engine._config.session_id,
        model=engine._config.effective_model,
        cwd=engine._config.cwd,
        turn_count=engine._turn_count,
        total_tokens=engine.token_tracker.get_total_tokens(),
        permission_mode=effective_permission_mode(engine),
        summary=session_summary(engine),
        updated_at=session_updated_at(engine),
    )


def disk_session_info(item: dict[str, Any]) -> SessionInfo:
    return SessionInfo(
        session_id=str(item["session_id"]),
        model="",
        cwd=str(item.get("cwd") or ""),
        turn_count=int(item.get("turn_count") or 0),
        total_tokens=0,
        permission_mode=normalize_permission_mode(str(item.get("permission_mode") or "")),
        summary=str(item.get("summary") or ""),
        updated_at=float(item.get("last_modified") or 0),
    )
