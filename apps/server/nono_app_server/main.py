"""产品 sidecar 入口 —— 本机绑定 + 桌面权限回调 + 额外路由。"""

from __future__ import annotations

import logging
import socket
import sys
from typing import Any

from nono_agent.adapters.server import create_server_app
from nono_app_server.config import load_engine_config, load_sidecar_settings
from nono_app_server.extra_routes import mount_extra_routes
from nono_app_server.permissions import desktop_permission_checker

logger = logging.getLogger(__name__)


def create_app() -> Any:
    """组装 FastAPI 应用：引擎 Server 适配器 + /app 产品路由。"""
    settings = load_sidecar_settings()
    config, settings = load_engine_config(settings)
    app = create_server_app(
        config,
        host=settings.host,
        port=settings.port,
        cors_origins=settings.cors_origins,
        permission_checker=desktop_permission_checker,
    )
    mount_extra_routes(app, settings, config)
    app.state.sidecar = settings
    return app


def main() -> None:
    """uvicorn 入口。默认只绑 127.0.0.1。"""
    logging.basicConfig(level=logging.INFO)
    settings = load_sidecar_settings()
    _, settings = load_engine_config(settings)
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError("uvicorn is required to run the sidecar") from exc

    logger.info(
        "Starting nono-app-server on %s:%s (cwd=%s, catalog_ready=%s)",
        settings.host,
        settings.port,
        settings.cwd,
        settings.catalog_ready,
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((settings.host, settings.port)) == 0:
            print(
                f"端口 {settings.host}:{settings.port} 已被占用。"
                "关掉占用进程，或设置 NONO_PORT 换端口。",
                file=sys.stderr,
            )
            sys.exit(1)
    uvicorn.run(
        "nono_app_server.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
