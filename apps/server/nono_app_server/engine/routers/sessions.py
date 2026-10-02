"""会话生命周期：创建、列表、权限、恢复、事件流、AskUser 回答。"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from nono_agent.session.header import VersionMismatchError, normalize_permission_mode
from nono_agent.session.stats import build_session_stats
from nono_agent.session.transcript import list_session_files
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.event_frames import (
    CHUNK_MODE_ALL,
    CHUNK_MODE_THINKING,
    build_frames,
    cached_frames,
    done_payload,
    store_frames,
)
from nono_app_server.engine.schemas import (
    AnswerRequest,
    NewSessionRequest,
    PermissionModeBody,
    ResumeRequest,
    SessionInfo,
    SessionStats,
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
    chunks: str = "all",
    manager: SessionManager = Depends(get_session_manager),
) -> Any:
    """按 SSE 推送该会话的事件流 —— 可观测性的读取通道。

    ``chunks=thinking`` 只推送思考增量，省掉正文分片那九成体积；会话查看器
    需要完整分片，默认仍为 ``all``。
    """
    writer = manager.events_writer(session_id)
    if writer is None:
        raise HTTPException(status_code=404, detail="Session log not found")

    chunk_mode = chunks if chunks in (CHUNK_MODE_ALL, CHUNK_MODE_THINKING) else CHUNK_MODE_ALL
    session_file = getattr(writer, "session_file", None)

    async def load_frames() -> list[tuple[int, str]]:
        """脱敏后的 ``(seq, json)`` 帧；同一份文件只算一次。"""
        if session_file is None:
            return build_frames(await writer.read_events_from(1), chunk_mode)
        hit = cached_frames(session_file, chunk_mode)
        if hit is not None:
            return hit
        frames = build_frames(await writer.read_events(), chunk_mode)
        store_frames(session_file, chunk_mode, frames)
        return frames

    async def event_generator() -> Any:
        try:
            frames = await load_frames()
        except VersionMismatchError as exc:
            yield {"event": "error", "data": json.dumps({"error": str(exc)})}
            return

        cursor = after_seq
        for seq, payload in frames:
            if seq <= after_seq:
                continue
            cursor = seq
            yield {"event": "session_event", "id": str(seq), "data": payload}

        if not follow:
            yield {"event": "done", "data": done_payload(cursor)}
            return

        while True:
            await asyncio.sleep(poll_interval)
            try:
                new_frames = build_frames(
                    await writer.read_events_from(cursor + 1), chunk_mode
                )
            except VersionMismatchError as exc:
                yield {"event": "error", "data": json.dumps({"error": str(exc)})}
                return
            for seq, payload in new_frames:
                cursor = seq
                yield {"event": "session_event", "id": str(seq), "data": payload}

    return EventSourceResponse(event_generator())


@router.get("/session/{session_id}/stats")
async def session_stats(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
) -> SessionStats:
    """会话级统计 —— 轮/步/用时/TTFT/TPS/token 用量/上下文占用。

    活跃会话直接问引擎的增量累加器（O(1)，每一步之后都可以取）；
    历史会话没有引擎实例，才回退成读整份 transcript 再折叠 —— 那是 O(全量)，
    长会话要几百毫秒，不适合放在轮询里。两条路走的是
    ``nono_agent.session.stats`` 里的同一份实现，口径一致。
    """
    engine = manager.sessions.get(session_id)
    if engine is not None:
        return SessionStats.model_validate(engine.session_stats())
    writer = manager.events_writer(session_id)
    if writer is None:
        raise HTTPException(status_code=404, detail="Session log not found")
    try:
        events = await writer.read_events()
    except VersionMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SessionStats.model_validate(build_session_stats(events))


@router.get("/session/{session_id}/stats/stream")
async def stream_session_stats(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
) -> Any:
    """按 SSE 推送会话统计 —— 引擎每落一步事件推一次。

    消费端不用轮询：``QueryEngine`` 在事件落盘后回调 ``SessionManager`` 扇出，
    这里只是把队列里的快照原样转发。客户端只应订阅**正在浏览**的那一个会话，
    切会话时关掉旧流即可。
    """
    queue = manager.subscribe_stats(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Session log not found")

    async def event_generator() -> Any:
        try:
            engine = manager.sessions.get(session_id)
            if engine is not None:
                # 先补一帧当前值，避免客户端要等到下一次落盘才有数。
                yield {"event": "stats", "data": json.dumps(engine.session_stats())}
            else:
                writer = manager.events_writer(session_id)
                events = await writer.read_events() if writer is not None else []
                yield {
                    "event": "stats",
                    "data": json.dumps(build_session_stats(events)),
                }
            while True:
                snapshot = await queue.get()
                yield {"event": "stats", "data": json.dumps(snapshot)}
        except asyncio.CancelledError:
            # 客户端断开：正常路径，不是错误。
            raise
        finally:
            manager.unsubscribe_stats(session_id, queue)

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
