"""引擎路由依赖：从 FastAPI app.state 取出会话管理器与指令表。"""

from __future__ import annotations

from typing import cast

from fastapi import Request, WebSocket

from nono_agent.commands import CommandRegistry
from nono_app_server.engine.session_manager import SessionManager


def get_session_manager(request: Request) -> SessionManager:
    return cast(SessionManager, request.app.state.engine_sessions)


def get_session_manager_ws(websocket: WebSocket) -> SessionManager:
    """WebSocket 路由专用的取法。

    WebSocket 作用域里**没有** ``Request`` —— FastAPI 不会给 WS 路由注入它，
    写 ``Depends(get_session_manager)`` 会在握手时报「缺参数」。只能从
    ``websocket.app.state`` 自己取。
    """
    return cast(SessionManager, websocket.app.state.engine_sessions)


def get_command_registry(request: Request) -> CommandRegistry:
    return cast(CommandRegistry, request.app.state.command_registry)
