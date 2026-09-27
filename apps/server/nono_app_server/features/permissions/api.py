"""权限确认 HTTP。"""

from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
)
from pydantic import BaseModel

from nono_app_server.features.permissions.service import (
    answer_permission,
    list_pending,
)

router = APIRouter()


class PermissionAnswer(BaseModel):
    allow: bool


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
