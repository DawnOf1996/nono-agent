"""引擎 HTTP / SSE 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from nono_agent.commands import CommandContext, CommandRegistry, parse_slash
from nono_agent.paths import resolve_session_cwd
from nono_agent.session.header import VersionMismatchError, normalize_permission_mode
from nono_agent.session.redact import redact_events
from nono_agent.session.transcript import list_session_files
from nono_app_server.engine.models import (
    AbortRequest,
    AnswerRequest,
    ChatRequest,
    CommandRequest,
    NewSessionRequest,
    PermissionModeBody,
    ResumeRequest,
    SessionInfo,
)
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.sse import drain_to_queue
from nono_app_server.engine.views import (
    disk_session_info,
    request_permission_mode,
    session_info,
)

logger = logging.getLogger(__name__)


def register_engine_routes(
    app: FastAPI,
    manager: SessionManager,
    command_registry: CommandRegistry,
) -> None:
    """把引擎 API 挂到 FastAPI 实例上。"""

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """健康检查端点 —— 返回服务状态和活跃会话数。"""
        return {"status": "ok", "sessions": len(manager.sessions)}

    @app.post("/chat")
    async def chat(request: ChatRequest) -> Any:
        """发送消息并以 SSE 流式返回响应。"""
        session_id = request.session_id or str(uuid.uuid4())
        engine = await manager.get_or_create(session_id, model=request.model)
        if request.permission_mode:
            request_permission_mode(engine, request.permission_mode)
        manager.touch(session_id)

        async def event_generator() -> Any:
            submit = engine.submit_message(
                request.message,
                attachments=request.attachments,
            )
            queue = manager.ask_signal.setdefault(session_id, asyncio.Queue())
            sdk_queue: asyncio.Queue[Any] = asyncio.Queue()
            sdk_task: asyncio.Task[None] = asyncio.create_task(
                drain_to_queue(submit, sdk_queue)
            )
            try:
                while True:
                    try:
                        signal = queue.get_nowait()
                        yield {
                            "event": "ask_user",
                            "data": json.dumps(signal, ensure_ascii=False),
                        }
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    try:
                        sdk_msg = sdk_queue.get_nowait()
                        yield {
                            "event": sdk_msg.type,
                            "data": json.dumps(sdk_msg.model_dump(), ensure_ascii=False),
                        }
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    if sdk_task.done():
                        while not sdk_queue.empty():
                            sdk_msg = sdk_queue.get_nowait()
                            yield {
                                "event": sdk_msg.type,
                                "data": json.dumps(sdk_msg.model_dump(), ensure_ascii=False),
                            }
                        break

                    await asyncio.sleep(0.01)
            except Exception as exc:
                logger.exception("Error in chat stream for session %s", session_id)
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(exc), "session_id": session_id}),
                }
            finally:
                if not sdk_task.done():
                    sdk_task.cancel()
                yield {"event": "done", "data": ""}

        return EventSourceResponse(event_generator())

    @app.post("/session/new")
    async def new_session(http_request: Request) -> SessionInfo:
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

    @app.get("/commands")
    async def list_commands() -> dict[str, Any]:
        """列出可在输入框用 ``/`` 调用的斜杠指令。"""
        items = []
        for name in command_registry.names():
            command = command_registry.find(name)
            if command is None:
                continue
            items.append({"name": name, "description": command.description})
        return {"commands": items}

    @app.post("/command")
    async def run_command(body: CommandRequest) -> dict[str, Any]:
        """执行斜杠指令，返回给 UI 展示的文本（不经过模型）。"""
        raw = body.line.strip()
        if raw and not raw.startswith("/"):
            raw = f"/{raw}"
        parsed = parse_slash(raw)
        if parsed is None:
            raise HTTPException(status_code=400, detail="Not a slash command")
        name, args = parsed
        engine = None
        cwd = resolve_session_cwd(body.cwd or manager.default_config.cwd)
        if body.session_id:
            engine = await manager.get_or_create(
                body.session_id, cwd=body.cwd or None
            )
            cwd = engine._config.cwd
        result = command_registry.execute(
            name, args, CommandContext(engine=engine, cwd=cwd)
        )
        return {"name": name, "args": args, "result": result}

    @app.get("/sessions")
    async def list_sessions() -> dict[str, Any]:
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

    @app.post("/session/{session_id}/permission")
    async def set_session_permission(
        session_id: str, body: PermissionModeBody
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

    @app.delete("/session/{session_id}")
    async def close_session(session_id: str) -> dict[str, str]:
        """关闭并移除指定会话 —— 释放该会话的引擎资源。"""
        async with manager.lock:
            engine = manager.sessions.pop(session_id, None)
        if engine:
            await engine.close()
            return {"status": "closed", "session_id": session_id}
        raise HTTPException(status_code=404, detail="Session not found")

    @app.post("/session/{session_id}/resume")
    async def resume(session_id: str, request: ResumeRequest | None = None) -> SessionInfo:
        """恢复历史会话 —— 通过 TranscriptWriter 协议加载并继续对话。"""
        req = request or ResumeRequest(session_id=session_id)
        engine, messages = await manager.resume(
            session_id,
            model=req.model,
            skip_system=req.skip_system,
        )
        info = session_info(engine)
        return info.model_copy(update={"turn_count": len(messages), "total_tokens": 0})

    @app.get("/session/{session_id}/events")
    async def session_events(
        session_id: str,
        after_seq: int = 0,
        follow: bool = True,
        poll_interval: float = 0.5,
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

    @app.post("/abort")
    async def abort_session(request: AbortRequest) -> dict[str, str]:
        """中止会话中正在进行的查询 —— 触发 AbortController 取消流式传输。"""
        async with manager.lock:
            engine = manager.sessions.get(request.session_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Session not found")
        engine.abort(request.reason)
        return {"status": "aborted", "session_id": request.session_id}

    @app.post("/session/{session_id}/answer")
    async def answer(session_id: str, body: AnswerRequest) -> dict[str, str]:
        """回应用户对 AskUser 事件的回答，唤醒等待中的流。"""
        pending = manager.pending_questions.get(session_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="No pending question for session")
        _, future = pending
        if future.done():
            raise HTTPException(status_code=409, detail="Question already answered")
        future.set_result(body.answers)
        return {"status": "answered", "session_id": session_id}
