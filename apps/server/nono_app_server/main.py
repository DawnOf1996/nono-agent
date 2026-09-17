"""产品 sidecar 进程入口 —— 本机绑定 + uvicorn。"""

from __future__ import annotations

import logging
import multiprocessing
import os
import socket
import sys
from pathlib import Path

from nono_app_server.app import create_app
from nono_app_server.config import load_engine_config, load_sidecar_settings

logger = logging.getLogger(__name__)

__all__ = ["create_app", "main"]

ENV_SIDECAR_LOG = "NONO_SIDECAR_LOG"


def _prepare_stdio() -> None:
    """windowed 冻结进程的 stdout/stderr 可能是 None，uvicorn 会因此崩溃。"""
    log_path = os.environ.get(ENV_SIDECAR_LOG, "").strip()
    if sys.stdout is not None and sys.stderr is not None:
        return
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        target = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    else:
        target = open(os.devnull, "w")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = target
    if sys.stderr is None:
        sys.stderr = target


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path = os.environ.get(ENV_SIDECAR_LOG, "").strip()
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


def main() -> None:
    """uvicorn 入口。默认只绑 127.0.0.1。"""
    multiprocessing.freeze_support()
    _prepare_stdio()
    _configure_logging()
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
    # 冻结 exe 不能走 factory 字符串导入；开发态同样直接实例化即可。
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
