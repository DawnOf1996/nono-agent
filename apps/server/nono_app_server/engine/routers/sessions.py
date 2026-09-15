"""会话生命周期：创建、列表、权限、恢复、事件流、AskUser 回答。"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from nono_agent.session.header import VersionMismatchError, normalize_permission_mode
from nono_agent.session.redact import redact_events
from nono_agent.session.transcript import list_session_files
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.schemas import (
    AnswerRequest,
    NewSessionRequest,
    PermissionModeBody,
    ResumeRequest,
    SessionInfo,
)
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.views import (
    disk_session_info,
    request_permission_mode,
    session_info,
)

router = APIRouter(tags=["engine"])


@router.post("/session/new")
async def new_session(
    http_request: Request,
    manager: SessionManager = Depends(get_session_manager),
) -> SessionInfo:
    """创建新会话并返回其 ID —— JSON 可指定 model 与工作空间 cwd。"""
    req = NewSessionRequest()
    content_type = http_request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            raw = await http_request.json()
        except Exception:
            raw = None
        if isinstance(raw, dict):
            req = NewSessionRequest.model_validate(raw)
    session_id = str(uuid.uuid4())
    engine = await manager.get_or_create(session_id, model=req.model, cwd=req.cwd)
    if req.permission_mode:
        request_permission_mode(engine, req.permission_mode)
    info = session_info(engine)
    return info.model_copy(update={"turn_count": 0, "total_tokens": 0})


@router.get("/sessions")
async def list_sessions(
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    """列出磁盘上的已保存会话，活跃引擎覆盖同 id 的实时字段。

    尚未发送内容的临时会话只活在进程里，不出现在列表中。
    """
    live: dict[str, SessionInfo] = {}
    async with manager.lock:
        for sid, engine in manager.sessions.items():
            live[sid] = session_info(engine)

    result: list[SessionInfo] = []
    seen: set[str] = set()
    persisted = sorted(
        list_session_files(),
        key=lambda item: float(item.get("last_modified") or 0),
        reverse=True,
    )
    for item in persisted:
        sid = str(item["session_id"])
        seen.add(sid)
        result.append(live[sid] if sid in live else disk_session_info(item))
    for sid, info in live.items():
        if sid in seen:
            continue
        engine = manager.sessions.get(sid)
        if engine is None or not engine.is_persisted:
            continue
        result.append(info)
    return {"sessions": result}


@router.post("/session/{session_id}/permission")
async def set_session_permission(
    session_id: str,
    body: PermissionModeBody,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    """请求切换会话权限模式，下一次 turn/step 生效。"""
    mode = normalize_permission_mode(body.mode)
    if body.mode and mode != body.mode.strip():
        raise HTTPException(status_code=400, detail="Unknown permission mode")
    async with manager.lock:
        engine = manager.sessions.get(session_id)
    if engine is None:
        raise HTTPException(status_code=404, detail="Session not found")
    effective = request_permission_mode(engine, mode)
    pending = engine.permission_policy.pending_mode
    return {
        "session_id": session_id,
        "mode": engine.permission_policy.mode.value,
        "pending": pending.value if pending is not None else None,
        "effective": effective,
    }


@router.delete("/session/{session_id}")
async def close_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, str]:
    """关闭并移除指定会话 —— 释放该会话的引擎资源。"""
    async with manager.lock:
        engine = manager.sessions.pop(session_id, None)
    if engine:
        await engine.close()
        return {"status": "closed", "session_id": session_id}
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/session/{session_id}/resume")
async def resume(
    session_id: str,
    request: ResumeRequest | None = None,
    manager: SessionManager = Depends(get_session_manager),
) -> SessionInfo:
    """恢复历史会话 —— 通过 TranscriptWriter 协议加载并继续对话。"""
    req = request or ResumeRequest(session_id=session_id)
    engine, messages = await manager.resume(
        session_id,
        model=req.model,
        skip_system=req.skip_system,
    )
    info = session_info(engine)
    return info.model_copy(update={"turn_count": len(messages), "total_tokens": 0})


@router.get("/session/{session_id}/events")
async def session_events(
    session_id: str,
    after_seq: int = 0,
    follow: bool = True,
    poll_interval: float = 0.5,
    manager: SessionManager = Depends(get_session_manager),
) -> Any:
    """按 SSE 推送该会话的事件流 —— 可观测性的读取通道。"""
    writer = manager.events_writer(session_id)
    if writer is None:
        raise HTTPException(status_code=404, detail="Session log not found")

    async def event_generator() -> Any:
        cursor = after_seq
        while True:
            try:
                events = await writer.read_events_from(cursor + 1)
            except VersionMismatchError as exc:
                yield {"event": "error", "data": json.dumps({"error": str(exc)})}
                return

            for event in redact_events(events):
                cursor = event.seq
                yield {
                    "event": "session_event",
                    "id": str(event.seq),
                    "data": event.model_dump_json(),
                }

            if not follow:
                yield {"event": "done", "data": json.dumps({"last_seq": cursor})}
                return
            await asyncio.sleep(poll_interval)

    return EventSourceResponse(event_generator())


@router.post("/session/{session_id}/answer")
async def answer(
    session_id: str,
    body: AnswerRequest,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, str]:
    """回应用户对 AskUser 事件的回答，唤醒等待中的流。"""
    pending = manager.pending_questions.get(session_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="No pending question for session")
    _, future = pending
    if future.done():
        raise HTTPException(status_code=409, detail="Question already answered")
    future.set_result(body.answers)
    return {"status": "answered", "session_id": session_id}
