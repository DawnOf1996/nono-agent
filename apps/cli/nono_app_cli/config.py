"""从 YAML 目录与环境变量组装 EngineConfig。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nono_agent.core.config import EngineConfig, ProviderConfig
from nono_agent.paths import resolve_session_cwd
from nono_agent.providers.catalog import load_catalog
from nono_agent.session.transcript import peek_session_header

ENV_MODEL = "NONO_MODEL"
ENV_CWD = "NONO_CWD"
ENV_LOG_API_REQUESTS = "NONO_LOG_API_REQUESTS"


def env_truthy(name: str) -> bool:
    """环境变量是否为开启（1/true/yes/on，大小写不敏感）。"""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def coalesce(*values: Any) -> Any:
    """返回第一个非 None / 非空字符串的值。"""
    for v in values:
        if v is not None and v != "":
            return v
    return None


def resolve_resume_bindings(
    resume: str | None,
    cwd: str | None,
    *,
    session_dir: str | None = None,
) -> tuple[str | None, str | None]:
    """``--resume`` 时以 ``.header.json`` 为约束：header.cwd 覆盖 CLI ``--cwd``。

    Returns:
        ``(cwd, session_id)``。未 resume 时 session_id 为 None。
    """
    if not resume:
        return cwd, None
    header = peek_session_header(resume, Path(session_dir) if session_dir else None)
    if header is not None and header.cwd:
        return resolve_session_cwd(header.cwd), resume
    return cwd, resume


def build_engine_config(
    *,
    model: str | None = None,
    provider: str | None = None,
    cwd: str | None = None,
    verbose: bool = False,
    config_file: str | None = None,
    log_api_requests: bool = False,
) -> EngineConfig:
    """从 YAML 目录解析 EngineConfig。

    优先级：CLI ``--provider`` / ``--model`` > ``NONO_MODEL`` > ``--config`` overlay >
    项目 ``.nono-agent/config.yaml`` > 用户 ``~/.nono-agent/config.yaml``。
    密钥按 ``api_key_env`` 从进程环境或 ``~/.nono-agent/credentials`` 读取。
    """
    resolved_cwd = resolve_session_cwd(coalesce(cwd, os.environ.get(ENV_CWD)) or ".")
    catalog = load_catalog(resolved_cwd, overlay_path=config_file)
    resolved_model = coalesce(model, os.environ.get(ENV_MODEL))
    pc: ProviderConfig = catalog.resolve(
        provider_id=provider,
        model_id=resolved_model,
    )
    dump_api = log_api_requests or env_truthy(ENV_LOG_API_REQUESTS)
    if dump_api and not pc.log_api_requests:
        pc = pc.model_copy(update={"log_api_requests": True})
    return EngineConfig(
        model=pc.model_id,
        cwd=resolved_cwd,
        model_context=pc.model_context,
        verbose=verbose,
        provider=pc,
        include_partial_messages=True,  # 启用流式事件，实现实时文本渲染
        log_api_requests=dump_api,
    )
