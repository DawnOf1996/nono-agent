"""MCP 客户端 —— 官方 SDK 2.x 生命周期与 tools 桥接。"""

from nono_agent.mcp.bridge import MCPTool, bridge_mcp_server, tools_from_client
from nono_agent.mcp.client import MCPClient, MCPClientError, describe_exception
from nono_agent.mcp.config import (
    MCPServerConfig,
    dump_server_config,
    load_mcp_config,
    load_user_mcp_config,
    normalize_transport,
    save_user_mcp_config,
    user_mcp_path,
)
from nono_agent.mcp.launcher import LaunchRecipe, MCPLaunchError, resolve_launch, synthesize_env
from nono_agent.mcp.policy import MCPServerPolicy, resolve_server_policy
from nono_agent.mcp.runtime import MCPRuntime, MCPServerStatus, start_mcp_runtime

__all__ = [
    "LaunchRecipe",
    "MCPClient",
    "MCPClientError",
    "MCPLaunchError",
    "MCPRuntime",
    "MCPServerConfig",
    "MCPServerPolicy",
    "MCPServerStatus",
    "MCPTool",
    "bridge_mcp_server",
    "describe_exception",
    "dump_server_config",
    "load_mcp_config",
    "load_user_mcp_config",
    "normalize_transport",
    "save_user_mcp_config",
    "user_mcp_path",
    "resolve_launch",
    "resolve_server_policy",
    "start_mcp_runtime",
    "synthesize_env",
    "tools_from_client",
]
