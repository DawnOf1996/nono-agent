"""组装引擎 FastAPI 应用：会话管理 + HTTP/SSE 路由。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from nono_agent.commands import CommandRegistry
from nono_agent.commands.builtin import create_builtin_commands
from nono_agent.core.config import EngineConfig
from nono_agent.tools.context import PermissionChecker
from nono_app_server.engine.auth import install_api_key_auth
from nono_app_server.engine.routers import register_engine_routes
from nono_app_server.engine.session_manager import (
    SessionManager,
    deny_all_permission_checker,
)

logger = logging.getLogger(__name__)

__all__ = ["create_server_app", "run_server"]


def create_server_app(
    config: EngineConfig | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    cors_origins: list[str] | None = None,
    permission_checker: PermissionChecker | None = deny_all_permission_checker,
    permission_checker_factory: Callable[[str], PermissionChecker] | None = None,
    permission_canceller: Callable[[str], Any] | None = None,
    api_key: str | None = None,
    session_ttl: float | None = None,
    session_ttl_check_interval: float = 60.0,
) -> Any:
    """创建 FastAPI 应用实例 —— 提供 agent 的 HTTP API。

    Args:
        config: 默认引擎配置
        host: 服务器绑定地址
        port: 服务器绑定端口
        cors_origins: 允许的 CORS 来源列表
        permission_checker: 权限检查器，默认拒绝所有写操作。
            传入 None 表示不检查（宽松模式），或传入自定义函数。
        permission_checker_factory: 按 session_id 生成权限检查器；设置后优先于
            ``permission_checker``，用于让每个会话各自挂起等待授权。
        api_key: 可选鉴权密钥。设置后除 /health 外均需
            ``Authorization: Bearer <key>`` 或 ``X-API-Key: <key>``。
        session_ttl: 会话空闲超时（秒）。超过后后台任务清理并关闭会话。
        session_ttl_check_interval: TTL 清理扫描间隔（秒）。
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    manager = SessionManager(
        config or EngineConfig(),
        permission_checker,
        permission_checker_factory=permission_checker_factory,
        permission_canceller=permission_canceller,
        session_ttl=session_ttl,
        session_ttl_check_interval=session_ttl_check_interval,
    )

    command_registry = CommandRegistry()
    for command in create_builtin_commands(command_registry):
        command_registry.register(command)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        logger.info("Nono Agent Server starting up...")
        ttl_task: asyncio.Task[None] | None = None
        if session_ttl is not None:
            ttl_task = asyncio.create_task(manager.ttl_cleanup_task())
        yield
        if ttl_task is not None:
            ttl_task.cancel()
        logger.info("Shutting down — closing %d sessions...", len(manager.sessions))
        async with manager.lock:
            for sid, engine in list(manager.sessions.items()):
                try:
                    await engine.close()
                except Exception:
                    logger.exception("Error closing session %s", sid)
                del manager.sessions[sid]
        logger.info("Server shutdown complete.")

    app = FastAPI(
        title="Nono Agent Server",
        description="AI Agent Engine API — streaming responses via SSE",
        version="0.1.0",
        lifespan=lifespan,
    )

    install_api_key_auth(app, api_key)

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_engine_routes(app, manager, command_registry)
    _ = (host, port)
    return app


def run_server(
    config: EngineConfig | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    cors_origins: list[str] | None = None,
) -> None:
    """便捷函数 —— 创建并启动 uvicorn HTTP 服务器。"""
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn is required to run the server. Install with: pip install uvicorn"
        ) from None

    app = create_server_app(config, host=host, port=port, cors_origins=cors_origins)
    uvicorn.run(app, host=host, port=port)
