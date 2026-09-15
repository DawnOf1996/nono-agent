"""产品路由依赖：sidecar 配置、引擎默认配置、SQLite 产品库。"""

from __future__ import annotations

from fastapi import Request

from nono_agent.core.config import EngineConfig
from nono_app_server.config import SidecarSettings
from nono_app_server.product.store import AppStore


def get_sidecar(request: Request) -> SidecarSettings:
    return request.app.state.sidecar


def get_engine_config(request: Request) -> EngineConfig:
    return request.app.state.engine_config


def get_store(request: Request) -> AppStore:
    return request.app.state.app_store
