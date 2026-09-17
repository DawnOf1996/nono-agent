"""POST /chat、POST /abort —— 对话流与中止。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.schemas import AbortRequest, ChatRequest
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.sse import drain_to_queue
from nono_app_server.engine.views import request_permission_mode

logger = logging.getLogger(__name__)

router = APIRouter(tags=["engine"])


@router.post("/chat")
async def chat(
    request: ChatRequest,
    manager: SessionManager = Depends(get_session_manager),
) -> Any:
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


@router.post("/abort")
async def abort_session(
    request: AbortRequest,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, str]:
    """中止会话中正在进行的查询 —— 触发 AbortController 取消流式传输。"""
    async with manager.lock:
        engine = manager.sessions.get(request.session_id)
    if engine is None:
        raise HTTPException(status_code=404, detail="Session not found")
    manager.cancel_permissions(request.session_id)
    engine.abort(request.reason)
    return {"status": "aborted", "session_id": request.session_id}
