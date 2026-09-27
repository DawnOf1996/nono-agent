"""/app/changes：每轮文件变更的摘要、逐文件对比与还原。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from nono_app_server.product.changes import WorkspaceChangesService

router = APIRouter()


class RevertBody(BaseModel):
    index: int
    force: bool = False


def _service(request: Request) -> WorkspaceChangesService:
    service = getattr(request.app.state, "turn_observer", None)
    if not isinstance(service, WorkspaceChangesService):
        raise HTTPException(status_code=503, detail="变更记录不可用")
    return service


@router.get("/changes/{session_id}")
async def list_turn_changes(session_id: str, request: Request) -> dict[str, Any]:
    return {"turns": _service(request).list_turns(session_id)}


@router.get("/changes/{session_id}/{turn_id}/diff")
async def turn_file_diff(
    session_id: str,
    turn_id: str,
    request: Request,
    index: int = Query(ge=0),
    context: int = Query(default=3, ge=0, le=100_000),
) -> dict[str, Any]:
    result = _service(request).file_diff(session_id, turn_id, index, context)
    if result is None:
        raise HTTPException(status_code=404, detail="找不到这条改动")
    return result


@router.post("/changes/{session_id}/{turn_id}/revert")
async def revert_turn_file(
    session_id: str, turn_id: str, body: RevertBody, request: Request
) -> dict[str, Any]:
    try:
        return _service(request).revert_file(session_id, turn_id, body.index, force=body.force)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
