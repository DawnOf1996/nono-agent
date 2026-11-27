"""request/header 快照 —— 记录「模型当时被告诉了什么」。

每次发给 LLM 的请求配置（provider/model/temperature/system/tools）都要能
从日志重放。header 只在真变化时写：initial（首次 + 无历史）、resume（首次 +
有历史）、change（非首次 + 与最新一条字节级不等）。
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.session.events import (
    REQUEST_HEADER_REASON_CHANGE,
    REQUEST_HEADER_REASON_INITIAL,
    REQUEST_HEADER_REASON_RESUME,
)


def canonical_header(
    *,
    config: dict[str, Any] | None = None,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """规范化请求配置快照 —— 保证字节级可比。

    空 tools 会被消掉（空变非空才算 change）。
    """
    return {
        "config": dict(sorted((config or {}).items())),
        "system": system or "",
        "tools": list(tools or []),
    }


def header_equals(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """比对两个 header 是否字节级相等（config / system / tools[] 有序）。"""
    if a is b:
        return True
    if not a or not b:
        return False
    return (
        json.dumps(a.get("config"), sort_keys=True) == json.dumps(b.get("config"), sort_keys=True)
        and a.get("system") == b.get("system")
        and json.dumps(a.get("tools"), sort_keys=True) == json.dumps(b.get("tools"), sort_keys=True)
    )


def next_reason(
    baseline: dict[str, Any] | None,
    new: dict[str, Any],
    *,
    instance_logged: bool,
) -> str | None:
    """返回是否要写一条 request/header 及其 reason；None 表示不写。

    语义（对齐 deepseek-harness）：
    - 本实例第一次请求 + 日志无 header → initial
    - 本实例第一次请求 + 日志有 header → resume
    - 非首次 + 与最新一条不等 → change
    - 非首次 + 相等 → 不写
    """
    if not instance_logged:
        return REQUEST_HEADER_REASON_INITIAL if baseline is None else REQUEST_HEADER_REASON_RESUME
    if baseline is None or not header_equals(baseline, new):
        return REQUEST_HEADER_REASON_CHANGE
    return None


__all__ = ["canonical_header", "header_equals", "next_reason"]
