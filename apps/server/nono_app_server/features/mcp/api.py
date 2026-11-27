"""MCP 清单 HTTP。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import (
    BaseModel,
    Field,
)
from sse_starlette.sse import EventSourceResponse

from nono_agent.mcp.config import (
    load_user_mcp_config,
    save_user_mcp_config,
)
from nono_agent.mcp.runtime import collect_mcp_servers
from nono_agent.mcp.runtimes import prepare_stdio_runtime
from nono_agent.settings import patch_project_settings
from nono_app_server.common.deps import get_sidecar
from nono_app_server.config import SidecarSettings
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.mcp.schemas import McpServerUpsert
from nono_app_server.features.mcp.service import (
    _config_from_body,
    _validate_server_id,
    public_mcp,
)

router = APIRouter()


class McpEnabledBody(BaseModel):
    cwd: str
    id: str
    enabled: bool


class McpRuntimePrepare(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)


@router.get("/mcp")
async def get_mcp(
    cwd: str | None = None,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    path = (cwd or "").strip()
    return public_mcp(path, manager)


@router.post("/mcp/runtimes/prepare")
async def prepare_mcp_runtime(body: McpRuntimePrepare) -> EventSourceResponse:
    command = (body.command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="stdio MCP 需要填写 command")
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def report(message: str, percent: int | None) -> None:
        payload: dict[str, Any] = {"event": "progress", "message": message}
        if percent is not None:
            payload["percent"] = percent
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    def job() -> None:
        try:
            result = prepare_stdio_runtime(command, list(body.args), progress=report)
            loop.call_soon_threadsafe(
                queue.put_nowait, {"event": "done", "message": "运行时已就绪", **result}
            )
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, {"event": "error", "message": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def events():
        worker = asyncio.create_task(asyncio.to_thread(job))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event = str(item.pop("event", "progress"))
                yield {"event": event, "data": json.dumps(item, ensure_ascii=False)}
        finally:
            await worker

    return EventSourceResponse(events(), ping=15)


@router.post("/mcp/servers")
async def upsert_mcp_server(
    body: McpServerUpsert,
    cwd: str | None = None,
    settings: SidecarSettings = Depends(get_sidecar),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    try:
        config = _config_from_body(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    servers = load_user_mcp_config()
    servers[config.name] = config
    save_user_mcp_config(servers)
    path = (cwd or "").strip() or settings.cwd
    await manager.reload_mcp_for_cwd(path)
    return public_mcp(path, manager)


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(
    server_id: str,
    cwd: str | None = None,
    settings: SidecarSettings = Depends(get_sidecar),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    try:
        sid = _validate_server_id(server_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    servers = load_user_mcp_config()
    if sid not in servers:
        raise HTTPException(status_code=404, detail="MCP server not found")
    del servers[sid]
    save_user_mcp_config(servers)
    path = (cwd or "").strip() or settings.cwd
    await manager.reload_mcp_for_cwd(path)
    return public_mcp(path, manager)


@router.post("/mcp/enabled")
async def set_mcp_enabled(
    body: McpEnabledBody,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    cwd = body.cwd.strip()
    if not cwd:
        raise HTTPException(status_code=400, detail="cwd is required")
    try:
        sid = _validate_server_id(body.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    servers = collect_mcp_servers(cwd)
    if sid not in servers:
        raise HTTPException(status_code=404, detail="MCP server not found")
    patch_project_settings(cwd, {"mcp": {"servers": {sid: {"enabled": body.enabled}}}})
    await manager.reload_mcp_for_cwd(cwd)
    return public_mcp(cwd, manager)
