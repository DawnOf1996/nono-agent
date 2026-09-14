"""产品层路由：sidecar 元信息、模型目录与桌面权限确认。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from nono_agent.core.config import EngineConfig
from nono_app_server import __version__
from nono_app_server.catalog_api import (
    DefaultModelBody,
    ProviderUpsert,
    public_catalog,
    set_default_model,
    upsert_provider,
)
from nono_app_server.config import SidecarSettings
from nono_app_server.permissions import answer_permission, list_pending


class PermissionAnswer(BaseModel):
    allow: bool


def mount_extra_routes(app: FastAPI, settings: SidecarSettings, config: EngineConfig) -> None:
    """把 /app/* 挂到 create_server_app 返回的 FastAPI 实例上。"""
    router = APIRouter(prefix="/app", tags=["app"])

    @router.get("/info")
    async def app_info() -> dict[str, Any]:
        return {
            "name": "nono-app-server",
            "version": __version__,
            "engine": "nono-agent",
            "host": settings.host,
            "port": settings.port,
            "cwd": settings.cwd,
            "model": config.model,
            "catalog_ready": settings.catalog_ready,
            "catalog_error": settings.catalog_error,
        }

    @router.get("/catalog")
    async def get_catalog() -> dict[str, Any]:
        return public_catalog(settings.cwd)

    @router.post("/catalog/providers")
    async def save_provider(body: ProviderUpsert) -> dict[str, Any]:
        try:
            upsert_provider(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return public_catalog(settings.cwd)

    @router.post("/catalog/default")
    async def save_default(body: DefaultModelBody) -> dict[str, Any]:
        try:
            set_default_model(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return public_catalog(settings.cwd)

    @router.get("/permissions")
    async def pending_permissions() -> dict[str, Any]:
        return {"permissions": list_pending()}

    @router.post("/permissions/{request_id}/answer")
    async def answer(request_id: str, body: PermissionAnswer) -> dict[str, Any]:
        ok = answer_permission(request_id, body.allow)
        if not ok:
            raise HTTPException(status_code=404, detail="No pending permission for request_id")
        return {"status": "answered", "request_id": request_id, "allow": body.allow}

    app.include_router(router)
