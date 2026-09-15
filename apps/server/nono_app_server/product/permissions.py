"""桌面端可交互权限检查：按会话挂起的待办队列 + /app/permissions 路由。

每条请求都绑定 session_id：某个会话等待授权时只挂起它自己的工具调用，
其他会话的对话继续流式输出。用户切回该会话确认后，挂起的调用继续执行。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

ENV_PERMISSION_TIMEOUT = "NONO_PERMISSION_TIMEOUT"

_pending: dict[str, tuple[dict[str, Any], asyncio.Future[bool]]] = {}


class PermissionAnswer(BaseModel):
    allow: bool


def _timeout_seconds() -> float | None:
    """挂起时长上限。默认不限时——等用户切回该会话再确认。"""
    raw = os.environ.get(ENV_PERMISSION_TIMEOUT, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%s, waiting without timeout", ENV_PERMISSION_TIMEOUT, raw)
        return None
    return value if value > 0 else None


def list_pending(session_id: str | None = None) -> list[dict[str, Any]]:
    """列出尚未回答的权限请求（不含 Future）。"""
    items = [info for info, _future in _pending.values()]
    if session_id is None:
        return items
    return [info for info in items if info.get("session_id") == session_id]


async def request_permission(
    session_id: str,
    tool_name: str,
    input_params: dict[str, Any],
) -> bool:
    """挂起当前会话的工具调用，直到用户回答或超时。"""
    request_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bool] = loop.create_future()
    info = {
        "request_id": request_id,
        "session_id": session_id,
        "tool_name": tool_name,
        "input_params": input_params,
        "created_at": time.time(),
    }
    _pending[request_id] = (info, future)
    logger.info("Permission requested: %s (%s) session=%s", tool_name, request_id, session_id)
    timeout = _timeout_seconds()
    try:
        if timeout is None:
            return await future
        return await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        logger.warning("Permission timed out: %s (%s)", tool_name, request_id)
        return False
    finally:
        _pending.pop(request_id, None)


def make_permission_checker(session_id: str) -> Any:
    """构建绑定到某个会话的权限检查器，供 SessionManager 注入引擎。"""

    async def checker(tool_name: str, input_params: dict[str, Any]) -> bool:
        return await request_permission(session_id, tool_name, input_params)

    return checker


async def desktop_permission_checker(tool_name: str, input_params: dict[str, Any]) -> bool:
    """无会话上下文时的兜底检查器。"""
    return await request_permission("", tool_name, input_params)


def answer_permission(request_id: str, allow: bool) -> bool:
    """回应一条待办权限。不存在或已完成后返回 False。"""
    item = _pending.get(request_id)
    if item is None:
        return False
    _info, future = item
    if future.done():
        return False
    future.set_result(allow)
    return True


def cancel_session_permissions(session_id: str) -> int:
    """会话被删除/归档时拒绝其挂起的权限，避免引擎一直卡住。"""
    cancelled = 0
    for info, future in list(_pending.values()):
        if info.get("session_id") != session_id or future.done():
            continue
        future.set_result(False)
        cancelled += 1
    return cancelled


@router.get("/permissions")
async def pending_permissions(
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return {"permissions": list_pending(session_id)}


@router.post("/permissions/{request_id}/answer")
async def answer(request_id: str, body: PermissionAnswer) -> dict[str, Any]:
    ok = answer_permission(request_id, body.allow)
    if not ok:
        raise HTTPException(status_code=404, detail="No pending permission for request_id")
    return {"status": "answered", "request_id": request_id, "allow": body.allow}
