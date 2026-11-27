"""GET /app/info、POST /app/data-dir —— sidecar 元信息与数据目录搬家。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nono_agent.core.config import EngineConfig
from nono_agent.datadir import (
    DataDirError,
    DataDirOccupiedError,
    migrate_data_dir,
)
from nono_agent.paths import read_data_dir_pointer, user_home
from nono_agent.session.transcript import active_session_dir
from nono_app_server import __version__
from nono_app_server.common.deps import get_engine_config, get_sidecar, get_store
from nono_app_server.common.store import AppStore
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
        "session_dir": str(active_session_dir()),
    }


@router.post("/data-dir")
async def update_data_dir(
    body: DataDirBody,
    store: AppStore = Depends(get_store),
) -> dict[str, Any]:
    """更改数据目录，把整个 ``.nono-agent`` 搬过去。

    搬完要重启才生效：会话事件流、app.db 和托管运行时都被当前进程占着，
    详见 :mod:`nono_agent.datadir`。
    """
    # 搬之前先把 WAL 落盘，让 app.db 自己就是一个完整的库（WAL 模式下建表和写入
    # 都先进 -wal，主库可能还是空的）。
    store.checkpoint()
    try:
        # 数据目录常有几百 MB（托管运行时 + 会话），同步搬会把整个事件循环冻住：
        # 控制流、任务板、终端 WebSocket 全都跟着卡住，前端看起来像应用死了。
        return await asyncio.to_thread(migrate_data_dir, body.path)  # type: ignore[return-value]
    except DataDirOccupiedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (DataDirError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
