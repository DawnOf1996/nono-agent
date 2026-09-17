"""Settings 合并 —— 深层字典合并与来源追踪。"""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典 —— override 中的值覆盖 base 中的同名键。

    嵌套字典递归合并，其余类型直接覆盖。
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_layered(*layers: dict[str, Any]) -> dict[str, Any]:
    """按优先级从低到高合并多层字典。"""
    result: dict[str, Any] = {}
    for layer in layers:
        result = deep_merge(result, layer)
    return result
