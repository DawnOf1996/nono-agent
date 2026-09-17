"""SDK 发 HTTP 前的请求体日志 —— 由 ``log_api_requests`` 开关控制。

记录的是协议转换与 ``extra_body`` 合并之后、SDK 即将发出 HTTP 时的 ``params``，
不是会话事件流里的 ``request/header``。默认关闭；打开后打 INFO 到
``nono_agent.providers.api_request``，不写入 transcript。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

LOGGER_NAME = "nono_agent.providers.api_request"
logger = logging.getLogger(LOGGER_NAME)

_REDACT_KEYS = frozenset(
    {
        "api_key",
        "api-key",
        "x-api-key",
        "authorization",
        "token",
        "secret",
        "password",
        "credential",
        "access_token",
    }
)


def enable_api_request_logging() -> None:
    """把本 logger 调到 INFO，并使 stderr 以 UTF-8 写出。

    Windows 默认 stderr 是 GBK：``json.dumps(ensure_ascii=False)`` 内存里是正确中文，
    落到 ``2> file`` 却被转成 GBK，编辑器按 UTF-8 打开就会看成乱码；emoji 还会变成
    ``\\U0001f44b``。把 stderr 改成 UTF-8 后，重定向文件与模型收到的字符串一致。
    """
    logger.setLevel(logging.INFO)
    stderr = sys.stderr
    encoding = (getattr(stderr, "encoding", None) or "").lower().replace("-", "")
    if encoding not in {"utf8", ""} and hasattr(stderr, "reconfigure"):
        try:
            stderr.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass


def redact(obj: Any) -> Any:
    """递归脱敏敏感键，其余结构原样保留。"""
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in _REDACT_KEYS:
                out[key] = "***"
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return [redact(item) for item in obj]
    return obj


def log_api_request(*, protocol: str, params: dict[str, Any]) -> None:
    """把即将发给模型 HTTP API 的 params 打成一行 JSON。"""
    enable_api_request_logging()
    payload = {
        "protocol": protocol,
        "params": redact(params),
    }
    logger.info(
        "llm_http_request %s",
        json.dumps(payload, ensure_ascii=False, default=str),
    )


def maybe_log_api_request(
    config: Any,
    *,
    protocol: str,
    params: dict[str, Any],
) -> None:
    """``ProviderConfig.log_api_requests`` 为真时才 dump。"""
    if config is not None and getattr(config, "log_api_requests", False):
        log_api_request(protocol=protocol, params=params)
