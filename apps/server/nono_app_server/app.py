"""组装完整 sidecar：引擎 HTTP/SSE API + /app 产品路由。"""

from __future__ import annotations

from typing import Any

from nono_agent.session.transcript import load_session_dir_setting
from nono_app_server.config import load_engine_config, load_sidecar_settings
from nono_app_server.engine import create_server_app
from nono_app_server.features import mount_product_routes
from nono_app_server.features.permissions.service import (
    cancel_session_permissions,
    desktop_permission_checker,
    make_permission_checker,
)
from nono_app_server.web_ui import mount_web_ui, resolve_web_dist


def create_app() -> Any:
    """组装 FastAPI 应用：引擎 HTTP/SSE API + /app 产品路由。"""
    load_session_dir_setting()
    settings = load_sidecar_settings()
    config, settings = load_engine_config(settings)
    app = create_server_app(
        config,
        host=settings.host,
        port=settings.port,
        cors_origins=settings.cors_origins,
        permission_checker=desktop_permission_checker,
        permission_checker_factory=make_permission_checker,
        permission_canceller=cancel_session_permissions,
    )
    mount_product_routes(app, settings, config)
    web_dist = resolve_web_dist()
    if web_dist is not None:
        mount_web_ui(app, web_dist)
    return app
