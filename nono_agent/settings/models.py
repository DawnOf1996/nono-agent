"""Settings 数据模型 —— 合并结果 + 来源追踪。"""

from __future__ import annotations

from typing import Any


class Settings:
    """合并后的设置视图 —— 单一读入口。

    各子系统通过 ``settings.get(...)`` 读取，禁止各自读文件。
    ``source_of(key)`` 返回最终生效值的来源，用于可观测性。
    """

    def __init__(self, values: dict[str, Any], sources: dict[str, str]) -> None:
        self._values = values
        self._sources = sources

    def get(self, key: str, default: Any = None) -> Any:
        """读取设置值（支持点分路径，如 ``permissions.mode``）。"""
        node: Any = self._values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def source_of(self, key: str) -> str | None:
        """返回该键最终生效值的来源（default/user/project/policy/flag）。"""
        return self._sources.get(key)

    def as_dict(self) -> dict[str, Any]:
        """返回合并后的完整字典（浅拷贝）。"""
        return dict(self._values)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __repr__(self) -> str:
        return f"Settings(sources={sorted(set(self._sources.values()))})"
