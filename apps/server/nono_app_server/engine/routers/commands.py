"""斜杠指令：GET /commands、POST /command。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from nono_agent.commands import CommandContext, CommandRegistry, parse_slash
from nono_agent.paths import resolve_session_cwd
from nono_app_server.engine.deps import get_command_registry, get_session_manager
from nono_app_server.engine.schemas import CommandRequest
from nono_app_server.engine.session_manager import SessionManager

router = APIRouter(tags=["engine"])


@router.get("/commands")
async def list_commands(
    command_registry: CommandRegistry = Depends(get_command_registry),
) -> dict[str, Any]:
    """列出可在输入框用 ``/`` 调用的斜杠指令。"""
    items = []
    for name in command_registry.names():
        command = command_registry.find(name)
        if command is None:
            continue
        items.append({"name": name, "description": command.description})
    return {"commands": items}


@router.post("/command")
async def run_command(
    body: CommandRequest,
    manager: SessionManager = Depends(get_session_manager),
    command_registry: CommandRegistry = Depends(get_command_registry),
) -> dict[str, Any]:
    """执行斜杠指令，返回给 UI 展示的文本（不经过模型）。"""
    raw = body.line.strip()
    if raw and not raw.startswith("/"):
        raw = f"/{raw}"
    parsed = parse_slash(raw)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Not a slash command")
    name, args = parsed
    engine = None
    cwd = resolve_session_cwd(body.cwd or manager.default_config.cwd)
    if body.session_id:
        engine = await manager.get_or_create(body.session_id, cwd=body.cwd or None)
        cwd = engine._config.cwd
    result = command_registry.execute(name, args, CommandContext(engine=engine, cwd=cwd))
    return {"name": name, "args": args, "result": result}
