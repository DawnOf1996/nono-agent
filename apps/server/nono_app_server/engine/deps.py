"""引擎路由依赖：从 FastAPI app.state 取出会话管理器与指令表。"""

from __future__ import annotations

from fastapi import Request

from nono_agent.commands import CommandRegistry
from nono_app_server.engine.session_manager import SessionManager


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.engine_sessions


def get_command_registry(request: Request) -> CommandRegistry:
    return request.app.state.command_registry
