"""工具注册表 —— 注册、查找、导出工具 Schema 供 LLM API 使用。"""

from __future__ import annotations

from typing import Any

from nono_agent.tools.protocol import Tool


class ToolNotFound(KeyError):
    """工具未找到异常 —— 查询不存在的工具名时抛出。"""


class ToolRegistry:
    """工具注册表 —— 管理 agent 可用的工具集合。

    核心功能：
    - register/unregister: 注册/移除工具
    - find: 按名称查找工具
    - to_api_schema: 导出所有工具的 API Schema（用于 LLM function calling）
    - partition_by_concurrency: 按并发安全性分割工具调用
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具实例 —— 名称必须唯一，重复注册抛出 ValueError。"""
        if tool.name in self._tools:
            raise ValueError(
                f"Tool '{tool.name}' is already registered. "
                "Unregister it first or use a different name."
            )
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> Tool:
        """按名称移除并返回工具 —— 不存在时抛出 ToolNotFound。"""
        if name not in self._tools:
            raise ToolNotFound(f"Tool '{name}' not found in registry")
        return self._tools.pop(name)

    def find(self, name: str) -> Tool:
        """按名称查找工具 —— 不存在时抛出 ToolNotFound。"""
        if name not in self._tools:
            raise ToolNotFound(
                f"Tool '{name}' not found. Available: {list(self._tools.keys())}"
            )
        return self._tools[name]

    def has(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    def all(self) -> list[Tool]:
        """返回所有已注册的工具列表。"""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """返回所有已注册工具的名称列表。"""
        return list(self._tools.keys())

    def to_api_schema(self) -> list[dict[str, Any]]:
        """导出所有工具的 API Schema —— 用于 LLM 请求的 tools 参数。"""
        return [tool.to_api_schema() for tool in self._tools.values()]

    def partition_by_concurrency(
        self, tool_uses: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按并发安全性分割工具调用 —— 核心并发控制逻辑。

        将 tool_use 块分为两组：
        - concurrent: 并发安全的工具，可以并行执行（如 read_file, glob）
        - sequential: 非并发安全的工具，必须顺序执行（如 bash, write_file）

        Args:
            tool_uses: [{"name": ..., "id": ..., "input": ...}] 格式的调用列表

        Returns:
            (concurrent_blocks, sequential_blocks) 二元组
        """
        concurrent: list[dict[str, Any]] = []
        sequential: list[dict[str, Any]] = []

        for tu in tool_uses:
            name = tu.get("name", "")
            tool = self._tools.get(name)
            # 未注册的工具或明确标记为非并发安全的工具归入顺序组
            if tool is None or not tool.is_concurrent_safe:
                sequential.append(tu)
            else:
                concurrent.append(tu)

        return concurrent, sequential

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
