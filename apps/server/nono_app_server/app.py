"""组装完整 sidecar：引擎 HTTP/SSE API + /app 产品路由。"""

from __future__ import annotations

from typing import Any

from nono_app_server.config import load_engine_config, load_sidecar_settings
from nono_app_server.engine import create_server_app
from nono_app_server.product import mount_product_routes
from nono_app_server.product.permissions import desktop_permission_checker


def create_app() -> Any:
    """组装 FastAPI 应用：引擎 HTTP/SSE API + /app 产品路由。"""
    settings = load_sidecar_settings()
    config, settings = load_engine_config(settings)
    app = create_server_app(
        config,
        host=settings.host,
        port=settings.port,
        cors_origins=settings.cors_origins,
        permission_checker=desktop_permission_checker,
    )
    mount_product_routes(app, settings, config)
    return app
