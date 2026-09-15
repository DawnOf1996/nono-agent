"""工作区：SQLite 列表，供前端侧栏使用。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.product.deps import get_store
from nono_app_server.product.store import AppStore

router = APIRouter()


class WorkspaceBody(BaseModel):
    path: str


class WorkspacePatchBody(BaseModel):
    name: str


@router.get("/workspaces")
async def list_workspaces(store: AppStore = Depends(get_store)) -> dict[str, Any]:
    return {"workspaces": store.list_workspaces()}


@router.post("/workspaces")
async def upsert_workspace(
    body: WorkspaceBody, store: AppStore = Depends(get_store)
) -> dict[str, Any]:
    path = body.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    return store.upsert_workspace(path)


@router.patch("/workspaces/{workspace_id}")
async def patch_workspace(
    workspace_id: str,
    body: WorkspacePatchBody,
    store: AppStore = Depends(get_store),
) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    updated = store.update_workspace(workspace_id, name=name)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return updated


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    store: AppStore = Depends(get_store),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, str]:
    engine_ids = store.engine_ids_for_workspace(workspace_id)
    if not store.delete_workspace(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    async with manager.lock:
        engines = [manager.sessions.pop(sid, None) for sid in engine_ids]
    for engine in engines:
        if engine is None:
            continue
        try:
            await engine.close()
        except Exception:
            pass
    return {"status": "deleted", "id": workspace_id}
