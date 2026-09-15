"""会话列表映射：产品行 <-> 引擎 session_id。详情仍走引擎事件流。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from nono_agent.session.header import normalize_permission_mode
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.views import request_permission_mode, session_info
from nono_app_server.product.deps import get_store
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
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _enrich(row, manager)


@router.patch("/sessions/{session_id}")
async def patch_app_session(
    session_id: str,
    body: PatchSessionBody,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    mode = body.permission_mode
    if mode:
        normalized = normalize_permission_mode(mode)
        if mode.strip() and normalized != mode.strip():
            raise HTTPException(status_code=400, detail="Unknown permission mode")
        mode = normalized
        mapped = store.get_session(session_id)
        if mapped is not None:
            engine = manager.sessions.get(mapped["engine_session_id"])
            if engine is not None:
                request_permission_mode(engine, mode)
    row = store.update_session(
        session_id,
        title=body.title,
        model=body.model,
        permission_mode=mode,
        turn_count=body.turn_count,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
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
    async with manager.lock:
        engine = manager.sessions.pop(engine_id, None)
    if engine is not None:
        await engine.close()
    return {"status": "deleted", "id": session_id}
