"""GET /app/info —— sidecar 元信息。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nono_agent.core.config import EngineConfig
from nono_agent.datadir import (
    DataDirError,
    DataDirOccupiedError,
    migrate_data_dir,
    suggested_data_dir,
)
from nono_agent.paths import read_data_dir_pointer, user_home
from nono_agent.session.transcript import active_session_dir
from nono_app_server import __version__
from nono_app_server.common.deps import get_engine_config, get_sidecar
from nono_app_server.config import SidecarSettings

router = APIRouter()


class DataDirBody(BaseModel):
    path: str


@router.get("/info")
async def app_info(
    settings: SidecarSettings = Depends(get_sidecar),
    config: EngineConfig = Depends(get_engine_config),
) -> dict[str, Any]:
    current = user_home()
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
        "data_dir": str(current),
        "data_dir_is_default": read_data_dir_pointer() is None,
        "data_dir_suggested": str(suggested_data_dir()),
        "session_dir": str(active_session_dir()),
    }


@router.post("/data-dir")
async def update_data_dir(body: DataDirBody) -> dict[str, Any]:
    """更改数据目录，把整个 ``.nono-agent`` 搬过去。

    搬完要重启才生效：会话事件流、app.db 和托管运行时都被当前进程占着，
    详见 :mod:`nono_agent.datadir`。
    """
    try:
        return migrate_data_dir(body.path)  # type: ignore[return-value]
    except DataDirOccupiedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (DataDirError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
