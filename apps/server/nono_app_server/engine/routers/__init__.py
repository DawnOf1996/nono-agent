"""引擎 HTTP / SSE 路由按资源拆分后在此组装。"""

from __future__ import annotations

from fastapi import FastAPI

from nono_agent.commands import CommandRegistry
from nono_app_server.engine.session_manager import SessionManager

from . import chat, commands, health, sessions


def register_engine_routes(
    app: FastAPI,
    manager: SessionManager,
    command_registry: CommandRegistry,
) -> None:
    """把引擎 API 挂到 FastAPI 实例上。"""
    app.state.engine_sessions = manager
    app.state.command_registry = command_registry
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(commands.router)
