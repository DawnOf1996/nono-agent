"""结构化日志配置 —— 基于 structlog。

用统一的 structlog 日志系统替代 TS 版中分散的 logEvent() / logForDebugging() 调用。
支持 JSON 输出（Server 模式）和控制台彩色输出（CLI 模式）。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    verbose: bool = False,
) -> None:
    """配置 structlog 日志系统。

    Args:
        level: 日志级别（DEBUG, INFO, WARNING, ERROR）
        json_output: 为 True 时输出 JSON 格式日志（Server 模式用）
        verbose: 为 True 时设置级别为 DEBUG
    """
    if verbose:
        level = "DEBUG"

    log_level = getattr(logging, level.upper(), logging.INFO)

    # structlog 处理器链 —— 按顺序处理日志上下文
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # JSON 输出 —— 适合生产环境和日志收集系统
        processors.append(structlog.processors.JSONRenderer())
    else:
        # 控制台彩色输出 —— 适合开发调试
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 配置标准库 logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
    )

    # 抑制第三方库的冗余日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def get_logger(name: str = "nono_agent") -> structlog.stdlib.BoundLogger:
    """获取结构化日志实例。

    用法::

        logger = get_logger("engine")
        logger.info("query_started", model="claude-sonnet-4-20250514", turn=1)
    """
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# 分析事件辅助函数（替代 TS 版的 logEvent）
# ---------------------------------------------------------------------------

_logger = get_logger("analytics")


def log_event(
    event_name: str,
    **kwargs: Any,
) -> None:
    """记录分析事件。

    替代 TS 版中分散在代码库各处的 logEvent() 调用。
    生产环境中可接入真实的分析后端。
    """
    _logger.info(event_name, **kwargs)


def log_error(
    error: Exception,
    **kwargs: Any,
) -> None:
    """记录带上下文的错误信息。"""
    _logger.error(
        "error",
        error_type=type(error).__name__,
        error_message=str(error),
        **kwargs,
    )
