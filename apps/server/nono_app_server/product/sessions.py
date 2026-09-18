"""会话列表映射：产品行 <-> 引擎 session_id。详情仍走引擎事件流。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from nono_agent.session.events import last_turn_number
from nono_agent.session.fork import fork_transcript
from nono_agent.session.header import normalize_permission_mode
from nono_agent.session.transcript import (
    DEFAULT_SESSION_DIR,
    HEADER_SUFFIX,
    JSONL_SUFFIX,
    JSONL_ZSTD_SUFFIX,
    LocalFileTranscriptWriter,
    existing_transcript_path,
)
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.views import request_permission_mode, session_info
from nono_app_server.product.deps import get_store
from nono_app_server.product.permissions import cancel_session_permissions
from nono_app_server.product.store import AppStore

router = APIRouter()


class CreateSessionBody(BaseModel):
    workspace_id: str
    model: str | None = None
    permission_mode: str | None = None
    title: str | None = None


class PatchSessionBody(BaseModel):
    title: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    turn_count: int | None = None
    archived: bool | None = None


class ForkSessionBody(BaseModel):
    boundary_seq: int | None = None


def _purge_session_files(engine_session_id: str) -> None:
    directory = DEFAULT_SESSION_DIR
    candidates = [
        existing_transcript_path(directory, engine_session_id),
        directory / f"{engine_session_id}{HEADER_SUFFIX}",
        directory / f"{engine_session_id}{JSONL_SUFFIX}",
        directory / f"{engine_session_id}{JSONL_ZSTD_SUFFIX}",
    ]
    seen: set[Any] = set()
    for path in candidates:
        if path is None or path in seen:
            continue
        seen.add(path)
        if path.is_file():
            path.unlink()


async def _close_engine(manager: SessionManager, engine_session_id: str) -> None:
    cancel_session_permissions(engine_session_id)
    async with manager.lock:
        engine = manager.sessions.pop(engine_session_id, None)
    if engine is not None:
        try:
            await engine.close()
        except Exception:
            pass


def _enrich(row: dict[str, Any], manager: SessionManager) -> dict[str, Any]:
    engine = manager.sessions.get(row["engine_session_id"])
    if engine is None:
        return row
    info = session_info(engine)
    return {
        **row,
        "model": info.model or row["model"],
        "permission_mode": info.permission_mode or row["permission_mode"],
        "cwd": info.cwd or row["cwd"],
    }


@router.get("/sessions")
async def list_app_sessions(
    workspace_id: str | None = Query(default=None),
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    rows = store.list_sessions(workspace_id)
    return {"sessions": [_enrich(row, manager) for row in rows]}


@router.post("/sessions")
async def create_app_session(
    body: CreateSessionBody,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    workspace = store.get_workspace(body.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if not str(workspace.get("path") or "").strip():
        raise HTTPException(status_code=400, detail="workspace has no path")
    mode = None
    if body.permission_mode:
        mode = normalize_permission_mode(body.permission_mode)
        if body.permission_mode.strip() and mode != body.permission_mode.strip():
            raise HTTPException(status_code=400, detail="Unknown permission mode")
    engine_session_id = str(uuid.uuid4())
    engine = await manager.get_or_create(
        engine_session_id, model=body.model, cwd=workspace["path"]
    )
    if mode:
        request_permission_mode(engine, mode)
    info = session_info(engine)
    try:
        row = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id=engine_session_id,
            title=(body.title or "").strip(),
            model=info.model,
            permission_mode=info.permission_mode,
            cwd=workspace["path"],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _enrich(row, manager)


@router.patch("/sessions/{session_id}")
async def patch_app_session(
    session_id: str,
    body: PatchSessionBody,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    mode = body.permission_mode
    model = body.model
    mapped = store.get_session(session_id)
    engine = (
        manager.sessions.get(mapped["engine_session_id"]) if mapped is not None else None
    )
    if mode:
        normalized = normalize_permission_mode(mode)
        if mode.strip() and normalized != mode.strip():
            raise HTTPException(status_code=400, detail="Unknown permission mode")
        mode = normalized
        if engine is not None:
            request_permission_mode(engine, mode)
    if model is not None and engine is not None:
        model = manager.apply_model(engine, model)
    row = store.update_session(
        session_id,
        title=body.title,
        model=model,
        permission_mode=mode,
        turn_count=body.turn_count,
        archived=body.archived,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.archived:
        await _close_engine(manager, row["engine_session_id"])
    return _enrich(row, manager)


def _fork_boundary(events: list[Any], requested: int | None) -> int:
    last = events[-1].seq if events else 0
    if requested is None:
        return last
    if requested < 0:
        raise HTTPException(status_code=400, detail="boundary_seq must be >= 0")
    return min(requested, last)


@router.post("/sessions/{session_id}/fork")
async def fork_app_session(
    session_id: str,
    body: ForkSessionBody | None = None,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    source = store.get_session(session_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Session not found")
    requested = body.boundary_seq if body is not None else None
    new_engine_id = str(uuid.uuid4())
    live = manager.sessions.get(source["engine_session_id"])
    writer = live.transcript_writer if live is not None else manager.events_writer(
        source["engine_session_id"]
    )
    events = await writer.read_events() if writer is not None else []
    boundary = _fork_boundary(events, requested)
    if live is not None and writer is not None:
        await live.fork_session(boundary, new_engine_id)
    elif writer is not None:
        destination = LocalFileTranscriptWriter(
            DEFAULT_SESSION_DIR / f"{new_engine_id}{JSONL_ZSTD_SUFFIX}"
        )
        await fork_transcript(writer, destination, boundary, new_engine_id)
    prefix = [event for event in events if event.seq <= boundary]
    title = (source["title"] or "").strip()
    row = store.create_session(
        workspace_id=source["workspace_id"],
        engine_session_id=new_engine_id,
        title=f"{title} 副本" if title else "新会话",
        model=source["model"],
        permission_mode=source["permission_mode"],
        turn_count=last_turn_number(prefix),
        cwd=source["cwd"],
    )
    return _enrich(row, manager)


@router.delete("/sessions/{session_id}")
async def delete_app_session(
    session_id: str,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, str]:
    row = store.delete_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    engine_id = row["engine_session_id"]
    await _close_engine(manager, engine_id)
    _purge_session_files(engine_id)
    return {"status": "deleted", "id": session_id}
