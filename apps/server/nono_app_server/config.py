"""从环境变量与用户/项目 YAML 目录组装 EngineConfig。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace

from nono_agent.core.config import EngineConfig, ProviderConfig
from nono_agent.paths import resolve_session_cwd
from nono_agent.providers.catalog import load_catalog

logger = logging.getLogger(__name__)

ENV_HOST = "NONO_HOST"
ENV_PORT = "NONO_PORT"
ENV_MODEL = "NONO_MODEL"
ENV_CWD = "NONO_CWD"
ENV_PROVIDER = "NONO_PROVIDER"
ENV_WEB_ORIGIN = "NONO_WEB_ORIGIN"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_WEB_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


@dataclass(frozen=True)
class SidecarSettings:
    """产品层进程配置 —— 与引擎 EngineConfig 分离。"""

    host: str
    port: int
    cwd: str
    cors_origins: list[str]
    catalog_ready: bool
    catalog_error: str | None


def _coalesce(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _cors_origins() -> list[str]:
    raw = os.environ.get(ENV_WEB_ORIGIN, "").strip()
    if not raw:
        return list(DEFAULT_WEB_ORIGINS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_sidecar_settings() -> SidecarSettings:
    """读取 sidecar 绑定地址与工作目录。"""
    cwd = resolve_session_cwd(_coalesce(os.environ.get(ENV_CWD)) or ".")
    port_raw = os.environ.get(ENV_PORT, str(DEFAULT_PORT)).strip()
    try:
        port = int(port_raw)
    except ValueError:
        logger.warning("Invalid %s=%s, falling back to %s", ENV_PORT, port_raw, DEFAULT_PORT)
        port = DEFAULT_PORT
    return SidecarSettings(
        host=os.environ.get(ENV_HOST, DEFAULT_HOST).strip() or DEFAULT_HOST,
        port=port,
        cwd=cwd,
        cors_origins=_cors_origins(),
        catalog_ready=False,
        catalog_error=None,
    )


def load_engine_config(settings: SidecarSettings) -> tuple[EngineConfig, SidecarSettings]:
    """解析 YAML 目录；目录未就绪时仍返回可启动的默认 EngineConfig。"""
    model = _coalesce(os.environ.get(ENV_MODEL))
    provider_id = _coalesce(os.environ.get(ENV_PROVIDER))
    try:
        catalog = load_catalog(settings.cwd)
        provider: ProviderConfig = catalog.resolve(
            provider_id=provider_id,
            model_id=model,
        )
        config = EngineConfig(
            model=provider.model_id,
            cwd=settings.cwd,
            model_context=provider.model_context,
            provider=provider,
            include_partial_messages=True,
        )
        return config, replace(settings, catalog_ready=True, catalog_error=None)
    except Exception as exc:
        logger.warning("LLM catalog not ready (%s); server will start without a provider", exc)
        config = EngineConfig(
            model=model or "claude-sonnet-4-20250514",
            cwd=settings.cwd,
            include_partial_messages=True,
        )
        return config, replace(settings, catalog_ready=False, catalog_error=str(exc))
