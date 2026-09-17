"""MCP → Tool Protocol 桥接 —— 把 MCP tools 暴露为内置工具。

工具名加前缀 ``mcp__<server>__`` 避免冲突。沿用全局权限模式，
不为 MCP 另加 deny / 路径过滤。
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.mcp.client import MCPClient, MCPClientError
from nono_agent.mcp.config import MCPServerConfig
from nono_agent.tools.protocol import ToolBase


class MCPTool(ToolBase):
    """包装单个 MCP 工具为 Tool Protocol。"""

    def __init__(
        self,
        client: MCPClient,
        server_name: str,
        tool_def: dict[str, Any],
    ) -> None:
        self._client = client
        self._server_name = server_name
        self._mcp_name = str(tool_def.get("name", "unknown"))
        self._description = str(tool_def.get("description", ""))
        schema = tool_def.get("inputSchema") or tool_def.get("input_schema")
        self._input_schema = (
            schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
        )

    @property
    def mcp_tool_name(self) -> str:
        return self._mcp_name

    @property
    def name(self) -> str:
        return f"mcp__{self._server_name}__{self._mcp_name}"

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def is_concurrent_safe(self) -> bool:
        return False

    @property
    def has_write_side_effects(self) -> bool:
        return True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        try:
            text = await self._client.call_tool(self._mcp_name, input_params)
            return ToolResult(
                tool_use_id="",
                content=text,
                tool_name=self.name,
            )
        except MCPClientError as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error: {exc}",
                is_error=True,
                tool_name=self.name,
            )


async def tools_from_client(
    client: MCPClient,
    server_name: str,
    *,
    disabled_tools: frozenset[str] | set[str] | None = None,
) -> list[MCPTool]:
    """从已启动的客户端列出并包装工具；``disabled_tools`` 为短名或桥接名。"""
    blocked = disabled_tools or frozenset()
    tools: list[MCPTool] = []
    for tool_def in await client.list_tools():
        tool = MCPTool(client, server_name, tool_def)
        short = tool.mcp_tool_name
        if short in blocked or tool.name in blocked:
            continue
        tools.append(tool)
    return tools


async def bridge_mcp_server(
    config: MCPServerConfig,
    *,
    workspace: str | None = None,
    data_dir: Any = None,
) -> tuple[list[MCPTool], MCPClient]:
    """启动一个 MCP 服务器并桥接其全部工具。"""
    client = MCPClient(config, workspace=workspace, data_dir=data_dir)
    await client.start()
    try:
        tools = await tools_from_client(client, config.name)
    except Exception:
        await client.close()
        raise
    return tools, client
