"""GET /app/info —— sidecar 元信息。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nono_agent.core.config import EngineConfig
from nono_agent.session.transcript import active_session_dir
from nono_app_server import __version__
from nono_app_server.common.deps import get_engine_config, get_sidecar
from nono_app_server.config import SidecarSettings
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.sessions.service import change_session_directory

router = APIRouter()


class SessionDirBody(BaseModel):
    path: str


@router.get("/info")
async def app_info(
    settings: SidecarSettings = Depends(get_sidecar),
    config: EngineConfig = Depends(get_engine_config),
) -> dict[str, Any]:
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
        "session_dir": str(active_session_dir()),
    }


@router.post("/session-dir")
async def update_session_dir(
    body: SessionDirBody,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    """更改会话文件保存目录，并把已有会话文件迁过去。"""
    try:
        return await change_session_directory(body.path, manager)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
