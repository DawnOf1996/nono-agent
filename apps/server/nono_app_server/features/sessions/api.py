"""会话列表 HTTP。详情仍走引擎事件流。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel

from nono_agent.session.events import last_turn_number
from nono_agent.session.fork import fork_transcript
from nono_agent.session.header import normalize_permission_mode
from nono_agent.session.transcript import (
    JSONL_ZSTD_SUFFIX,
    LocalFileTranscriptWriter,
    active_session_dir,
)
from nono_app_server.common.deps import get_store
from nono_app_server.common.store import AppStore
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.views import (
    request_permission_mode,
    session_info,
)
from nono_app_server.features.changes.service import WorkspaceChangesService
from nono_app_server.features.sessions.service import (
    _close_engine,
    _enrich,
    _fork_boundary,
    _fork_title,
    _note_interrupted,
    _purge_session_files,
)

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


class SideSessionBody(BaseModel):
    interrupt_text: str | None = None


@router.get("/sessions")
async def list_app_sessions(
    workspace_id: str | None = Query(default=None),
    side_of: str | None = Query(default=None),
    archived: str = Query(default="hide"),
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    if archived not in {"hide", "all", "only"}:
        raise HTTPException(status_code=400, detail="archived must be hide, all, or only")
    rows = store.list_sessions(workspace_id, side_of=side_of, archived=archived)
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
    engine = await manager.get_or_create(engine_session_id, model=body.model, cwd=workspace["path"])
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
    engine = manager.sessions.get(mapped["engine_session_id"]) if mapped is not None else None
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
    writer = (
        live.transcript_writer
        if live is not None
        else manager.events_writer(source["engine_session_id"])
    )
    events = await writer.read_events() if writer is not None else []
    boundary = _fork_boundary(events, requested)
    if live is not None and writer is not None:
        await live.fork_session(boundary, new_engine_id)
    elif writer is not None:
        destination = LocalFileTranscriptWriter(
            active_session_dir() / f"{new_engine_id}{JSONL_ZSTD_SUFFIX}"
        )
        await fork_transcript(writer, destination, boundary, new_engine_id)
    prefix = [event for event in events if event.seq <= boundary]
    # 编号和插入之间不能有 await：否则两次并发叉分会数到同一个号。
    title, fork_root = _fork_title(store, source)
    row = store.create_session(
        workspace_id=source["workspace_id"],
        engine_session_id=new_engine_id,
        title=title,
        model=source["model"],
        permission_mode=source["permission_mode"],
        turn_count=last_turn_number(prefix),
        cwd=source["cwd"],
        fork_root=fork_root,
    )
    return _enrich(row, manager)


@router.post("/sessions/{session_id}/side")
async def open_side_session(
    session_id: str,
    body: SideSessionBody | None = None,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    """从主会话分出一条侧边对话。它不出现在会话列表里，也不改主会话。"""
    forked = await fork_app_session(session_id, None, store, manager)
    hidden = store.set_side_of(forked["id"], session_id)
    if hidden is None:
        raise HTTPException(status_code=404, detail="Session not found")
    note = body.interrupt_text if body is not None else None
    if note:
        await _note_interrupted(hidden["engine_session_id"], note)
    titled = store.update_session(hidden["id"], title="侧边对话")
    return _enrich(titled or hidden, manager)


@router.post("/sessions/{session_id}/promote")
async def promote_side_session(
    session_id: str,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    """把侧边对话提升成顶层会话。"""
    current = store.get_session(session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # 编号得在摘掉侧边标记之前算：侧边对话不占号，提升成顶层时才拿到家族里的号。
    title = _fork_title(store, current)[0] if current["fork_root"] else None
    row = store.set_side_of(session_id, None)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if title is not None:
        row = store.update_session(session_id, title=title) or row
    return _enrich(row, manager)


@router.delete("/sessions/{session_id}")
async def delete_app_session(
    session_id: str,
    request: Request,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, str]:
    row = store.delete_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    engine_id = row["engine_session_id"]
    await _close_engine(manager, engine_id)
    _purge_session_files(engine_id)
    observer = getattr(request.app.state, "turn_observer", None)
    if isinstance(observer, WorkspaceChangesService):
        observer.forget_session(engine_id)
    return {"status": "deleted", "id": session_id}
