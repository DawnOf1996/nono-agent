"""MCP 启停与工具禁用 —— 按 server id 从 Settings 读取，不进 mcp.json。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nono_agent.settings import Settings


def as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return default


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _short_tool_name(server_id: str, raw: str) -> str:
    prefix = f"mcp__{server_id}__"
    if raw.startswith(prefix):
        return raw[len(prefix) :]
    return raw


@dataclass(frozen=True)
class MCPServerPolicy:
    """单个 MCP server 的策略视图。"""

    enabled: bool = False
    disabled_tools: frozenset[str] = frozenset()

    def allows_tool(self, server_id: str, tool_name: str) -> bool:
        return _short_tool_name(server_id, tool_name) not in self.disabled_tools


def resolve_server_policy(
    settings: Settings | None, server_id: str
) -> MCPServerPolicy:
    """读取 ``mcp.servers.<id>``：未声明则不启用、不禁用任何工具。"""
    if settings is None:
        return MCPServerPolicy()
    block = settings.get(f"mcp.servers.{server_id}")
    if not isinstance(block, dict):
        return MCPServerPolicy()
    disabled = [
        _short_tool_name(server_id, name)
        for name in _as_str_list(
            block.get("disabledTools", block.get("disabled_tools"))
        )
    ]
    return MCPServerPolicy(
        enabled=as_bool(block.get("enabled"), False),
        disabled_tools=frozenset(disabled),
    )
