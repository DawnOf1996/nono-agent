"""GET /app/info —— sidecar 元信息。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from nono_agent.core.config import EngineConfig
from nono_app_server import __version__
from nono_app_server.config import SidecarSettings
from nono_app_server.product.deps import get_engine_config, get_sidecar

router = APIRouter()


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
    }
