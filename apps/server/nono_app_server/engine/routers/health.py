"""GET /health。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager

router = APIRouter(tags=["engine"])


@router.get("/health")
async def health(
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    """健康检查端点 —— 返回服务状态和活跃会话数。"""
    return {"status": "ok", "sessions": len(manager.sessions)}
