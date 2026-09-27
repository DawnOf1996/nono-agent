"""按会话挂起的权限确认队列。某个会话等待授权时只堵住它自己。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

ENV_PERMISSION_TIMEOUT = "NONO_PERMISSION_TIMEOUT"

_pending: dict[str, tuple[dict[str, Any], asyncio.Future[bool]]] = {}


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
