"""工具插件 HTTP。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.toolplugins.schemas import (
    ToolPluginApiKey,
    ToolPluginUpsert,
)
from nono_app_server.features.toolplugins.service import (
    apply_upsert,
    public_tool_plugins,
    save_api_key,
)

router = APIRouter()


@router.get("/tool-plugins")
async def get_tool_plugins(
    cwd: str = "",
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    return public_tool_plugins((cwd or "").strip(), manager)


@router.put("/tool-plugins/{plugin_id}")
async def update_tool_plugin(
    plugin_id: str,
    body: ToolPluginUpsert,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    try:
        return await apply_upsert(plugin_id, body, manager)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/tool-plugins/{plugin_id}/api-key")
async def update_tool_plugin_api_key(
    plugin_id: str,
    body: ToolPluginApiKey,
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    try:
        return await save_api_key(plugin_id, body, manager)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tool-plugins/reload")
async def reload_tool_plugins(
    cwd: str = Query(default=""),
    manager: SessionManager = Depends(get_session_manager),
) -> dict[str, Any]:
    path = (cwd or "").strip()
    await manager.reload_extensions_for_cwd(path, mcp=False, tool_plugins=True)
    return public_tool_plugins(path, manager)
