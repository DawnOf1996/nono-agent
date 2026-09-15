"""桌面端可交互权限检查：待办队列 + /app/permissions 路由。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_pending: dict[str, tuple[dict[str, Any], asyncio.Future[bool]]] = {}


class PermissionAnswer(BaseModel):
    allow: bool


def list_pending() -> list[dict[str, Any]]:
    """列出尚未回答的权限请求（不含 Future）。"""
    return [info for info, _future in _pending.values()]


async def desktop_permission_checker(tool_name: str, input_params: dict[str, Any]) -> bool:
    """异步权限回调：写入待办队列，最多等待 300 秒。"""
    request_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bool] = loop.create_future()
    info = {
        "request_id": request_id,
        "tool_name": tool_name,
        "input_params": input_params,
        "created_at": time.time(),
    }
    _pending[request_id] = (info, future)
    logger.info("Permission requested: %s (%s)", tool_name, request_id)
    try:
        return await asyncio.wait_for(future, timeout=300)
    except TimeoutError:
        logger.warning("Permission timed out: %s (%s)", tool_name, request_id)
        return False
    finally:
        _pending.pop(request_id, None)


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


@router.get("/permissions")
async def pending_permissions() -> dict[str, Any]:
    return {"permissions": list_pending()}


@router.post("/permissions/{request_id}/answer")
async def answer(request_id: str, body: PermissionAnswer) -> dict[str, Any]:
    ok = answer_permission(request_id, body.allow)
    if not ok:
        raise HTTPException(status_code=404, detail="No pending permission for request_id")
    return {"status": "answered", "request_id": request_id, "allow": body.allow}
