"""产品 sidecar 进程入口 —— 本机绑定 + uvicorn。"""

from __future__ import annotations

import logging
import multiprocessing
import os
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
    # 这里刻意不做「端口是否被占用」的预检。Windows 上 connect 一个**空闲**回环
    # 端口要空等约 2s（本机装有拦截回环流量的 WFP filter 时尤其明显，连空闲端口
    # 恒定 2030~2050ms，连有监听的端口 0ms），而打包态几乎必然失败——Electron 刚用
    # pickFreePort() 选了个空端口传进来，预检只能白等。更关键的是预检本身是
    # TOCTOU：查完到真正 bind 之间仍有窗口，真正的竞态它一个都挡不住。占用情况交给
    # uvicorn 的 bind 报错兜底即可（见下方 except），语义不变。
    #
    # 冻结 exe 不能走 factory 字符串导入；开发态同样直接实例化即可。
    app = create_app()
    try:
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    except SystemExit:
        # uvicorn 的 bind_socket() 捕获 OSError 后 logger.error + sys.exit(1)，
        # 这里只补一条人话提示，不改退出码。
        print(
            f"端口 {settings.host}:{settings.port} 已被占用。"
            "关掉占用进程，或设置 NONO_PORT 换端口。",
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    main()
