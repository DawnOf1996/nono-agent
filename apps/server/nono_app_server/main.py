"""产品 sidecar 进程入口 —— 本机绑定 + uvicorn。"""

from __future__ import annotations

import logging
import socket
import sys

from nono_app_server.app import create_app
from nono_app_server.config import load_engine_config, load_sidecar_settings

logger = logging.getLogger(__name__)

__all__ = ["create_app", "main"]


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
        "nono_app_server.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
