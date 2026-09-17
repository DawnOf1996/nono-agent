"""脱敏投影 —— 把事件流投影成可外发的版本。

脱敏发生在**读取/导出侧**，不在写入侧：写入侧脱敏会同时破坏两条不变量 ——
``derive_messages()`` 重建不出运行时状态（请求前门禁必然报 desync），
以及 ``request/header`` 与实时计算出的 header 永远不等（每轮都误判为 change）。
因此磁盘上的事件流保持逐字真实（会话文件按 0600 落盘），
需要把日志给别人看时走本模块投影一份。

覆盖面是常见凭据形态的启发式匹配，不是完备的密钥检测：它降低误发概率，
不构成「事件流可以随便公开」的保证。
"""

from __future__ import annotations

import re
from typing import Any

from nono_agent.session.events import SessionEvent

# 脱敏占位符前缀 —— 保留命中的凭据种类，便于排障时判断“这里原本是什么”
REDACTED = "[redacted:{kind}]"

# (凭据种类, 匹配模式)；顺序即优先级，先匹配到的先替换
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
    ),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}")),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
)

# 形如 api_key="..." / token: ... 的赋值；只替换值，保留键名以便定位
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key"
    r"|client[_-]?secret|password|passwd|secret|token|authorization)"
    r"(\s*[:=]\s*)"
    r"(\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[A-Za-z0-9._~+/=-]{8,})"
)

# 键名本身就表明是凭据的字段，整值替换（不看内容形态）
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "client_secret",
        "password",
        "passwd",
        "refresh_token",
        "secret",
        "secret_key",
        "token",
    }
)


def redact_text(text: str) -> str:
    """替换文本中形似凭据的片段。

    Args:
        text: 原始文本。

    Returns:
        脱敏后的文本；无命中时原样返回。
    """
    for kind, pattern in _PATTERNS:
        text = pattern.sub(REDACTED.format(kind=kind), text)
    return _ASSIGNMENT.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED.format(kind='secret')}", text
    )


def redact_value(value: Any) -> Any:
    """递归脱敏任意 JSON 兼容值。

    字典键命中 ``_SENSITIVE_KEYS`` 时整值替换，其余走文本模式匹配。

    Args:
        value: 事件载荷中的任意值。

    Returns:
        脱敏后的同构值。
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: (
                REDACTED.format(kind="secret")
                if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_event(event: SessionEvent) -> SessionEvent:
    """返回载荷已脱敏的事件副本（信封字段不变）。

    Args:
        event: 原始事件。

    Returns:
        新的 SessionEvent；``seq`` / ``type`` / ``timestamp`` / ``ignorable`` 保持原值，
        因此脱敏后的事件流仍可按 seq 对齐原始日志。
    """
    return event.model_copy(update={"data": redact_value(event.data)})


def redact_events(events: list[SessionEvent]) -> list[SessionEvent]:
    """批量脱敏。

    Args:
        events: 原始事件列表。

    Returns:
        脱敏后的事件列表（顺序不变）。
    """
    return [redact_event(event) for event in events]


__all__ = [
    "REDACTED",
    "redact_event",
    "redact_events",
    "redact_text",
    "redact_value",
]
