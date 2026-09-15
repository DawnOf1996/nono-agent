"""产品层路由：sidecar 元信息、模型目录与桌面权限确认。"""

from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
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


class MkdirBody(BaseModel):
    parent: str
    name: str


class OpenPathBody(BaseModel):
    path: str


def _home() -> Path:
    return Path.home()


def _windows_drives() -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            drives.append({"name": f"{letter}:", "path": str(root), "is_dir": True})
    return drives


def _resolve_dir(raw: str | None) -> Path:
    if not raw or raw.strip() in {"", "~"}:
        return _home()
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_home() / path).resolve()
        else:
            path = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    return path


def _parent_path(path: Path) -> str | None:
    parent = path.parent
    if parent == path:
        return None
    return str(parent)


def _list_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        children = list(path.iterdir())
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                entries.append({"name": child.name, "path": str(child), "is_dir": True})
        except OSError:
            continue
    entries.sort(key=lambda item: item["name"].lower())
    return entries


def mount_extra_routes(app: FastAPI, settings: SidecarSettings, config: EngineConfig) -> None:
    """把 /app/* 挂到引擎 FastAPI 实例上。"""
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

    @router.get("/fs")
    async def list_directory(path: str | None = Query(default=None)) -> dict[str, Any]:
        """列出目录下的子文件夹，供工作区选择器浏览。"""
        raw = (path or "").strip()
        if os.name == "nt" and raw in {"", "/", "\\"}:
            return {
                "path": "",
                "parent": None,
                "home": str(_home()),
                "entries": _windows_drives(),
            }
        current = _resolve_dir(path)
        return {
            "path": str(current),
            "parent": _parent_path(current),
            "home": str(_home()),
            "entries": _list_entries(current),
        }

    @router.post("/fs/mkdir")
    async def make_directory(body: MkdirBody) -> dict[str, Any]:
        name = body.name.strip()
        if not name or any(sep in name for sep in ("/", "\\")) or name in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid folder name")
        parent = _resolve_dir(body.parent)
        target = parent / name
        try:
            target.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="Folder already exists") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"path": str(target), "parent": str(parent), "name": name}

    @router.post("/fs/open")
    async def open_directory(body: OpenPathBody) -> dict[str, Any]:
        """在系统文件管理器中打开目录。"""
        target = _resolve_dir(body.path)
        try:
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"path": str(target), "opened": True}

    app.include_router(router)
