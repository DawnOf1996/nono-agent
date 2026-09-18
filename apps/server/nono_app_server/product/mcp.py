"""用户级 MCP 清单与工作区启停。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from nono_agent.mcp.config import (
    MCPServerConfig,
    load_user_mcp_config,
    save_user_mcp_config,
    user_mcp_path,
)
from nono_agent.mcp.policy import as_bool, resolve_server_policy
from nono_agent.mcp.runtime import collect_mcp_servers
from nono_agent.mcp.runtimes import prepare_stdio_runtime
from nono_agent.paths import resolve_session_cwd
from nono_agent.settings import load_settings_from_env, patch_project_settings
from nono_app_server.config import SidecarSettings
from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.product.deps import get_sidecar

router = APIRouter()

_SERVER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class McpServerUpsert(BaseModel):
    id: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env_file: str | None = None
    cwd: str | None = None


class McpEnabledBody(BaseModel):
    cwd: str
    id: str
    enabled: bool


class McpRuntimePrepare(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)


def _validate_server_id(server_id: str) -> str:
    sid = server_id.strip()
    if not _SERVER_ID.match(sid):
        raise ValueError("id 需为小写字母开头，只能含小写字母、数字、下划线和连字符")
    return sid


def _config_from_body(body: McpServerUpsert) -> MCPServerConfig:
    sid = _validate_server_id(body.id)
    transport = (body.transport or "stdio").strip().lower()
    command = (body.command or "").strip() or None
    url = (body.url or "").strip() or None
    if transport in {"http", "sse"}:
        if not url:
            raise ValueError("远程 MCP 需要填写 url")
    elif not command:
        raise ValueError("stdio MCP 需要填写 command")
    return MCPServerConfig(
        name=sid,
        command=command,
        args=[str(item) for item in body.args if str(item).strip()],
        env={str(key): str(value) for key, value in body.env.items() if str(key).strip()},
        transport=transport,
        url=url,
        headers={
            str(key): str(value) for key, value in body.headers.items() if str(key).strip()
        },
        env_file=(body.env_file or "").strip() or None,
        cwd=(body.cwd or "").strip() or None,
    )


def _live_statuses(cwd: str, manager: SessionManager) -> dict[str, Any]:
    resolved = resolve_session_cwd(cwd)
    for engine in manager.sessions.values():
        if resolve_session_cwd(engine._config.cwd) != resolved:
            continue
        runtime = engine.mcp_runtime
        if runtime is None:
            continue
        return {status.name: status for status in runtime.statuses}
    return {}


def public_mcp(cwd: str, manager: SessionManager) -> dict[str, Any]:
    raw_cwd = (cwd or "").strip()
    settings = load_settings_from_env(raw_cwd) if raw_cwd else None
    user_servers = load_user_mcp_config()
    servers = collect_mcp_servers(raw_cwd)
    live = _live_statuses(raw_cwd, manager) if raw_cwd else {}
    items: list[dict[str, Any]] = []
    for name, cfg in servers.items():
        policy = resolve_server_policy(settings, name)
        status = live.get(name)
        items.append(
            {
                "id": name,
                "transport": cfg.normalized_transport,
                "command": cfg.command,
                "args": list(cfg.args),
                "env": dict(cfg.env),
                "url": cfg.url,
                "headers": dict(cfg.headers),
                "env_file": cfg.env_file,
                "cwd": cfg.cwd,
                "source": "user" if name in user_servers else "plugin",
                "enabled": policy.enabled,
                "running": bool(getattr(status, "running", False)),
                "tool_count": int(getattr(status, "tool_count", 0) or 0),
                "error": getattr(status, "error", None),
                "disabled_tools": sorted(policy.disabled_tools),
            }
        )
    return {
        "config_path": str(user_mcp_path()),
        "enabled": as_bool(settings.get("mcp.enabled", True) if settings else True),
        "servers": items,
    }


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
            loop.call_soon_threadsafe(
                queue.put_nowait, {"event": "error", "message": str(exc)}
            )
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
