"""MCP 客户端 —— 官方 SDK 2.x 生命周期与 tools 桥接。

这里刻意不做 eager re-export。Python 规定包的 ``__init__`` 先于任何子模块执行，所以只要
sidecar 启动链上有任何一个 ``from nono_agent.mcp.X import ...``（``features/mcp/api.py``
只要 ``load_user_mcp_config`` 这类纯 JSON 函数），eager re-export 就会把
``bridge``/``client`` 连同整个官方 MCP SDK 一起拖进启动路径——实测约 630ms，占 sidecar
启动的近三成。

改成 PEP 562 惰性导出后，对外 API 一字不变，但官方 SDK 只在真正被访问时才加载。
配套改动：``mcp/runtime.py``、``mcp/launcher.py``、``toolplugins/runtime.py`` 也各自把
SDK 的导入推迟到函数内部，否则它们仍会独立地把 SDK 拖回启动链。
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    from nono_agent.mcp.launcher import (
        LaunchRecipe,
        MCPLaunchError,
        resolve_launch,
        synthesize_env,
    )
    from nono_agent.mcp.policy import MCPServerPolicy, resolve_server_policy
    from nono_agent.mcp.runtime import MCPRuntime, MCPServerStatus, start_mcp_runtime

#: 导出名 -> 定义它的子模块。
_EXPORTS = {
    "LaunchRecipe": "launcher",
    "MCPClient": "client",
    "MCPClientError": "client",
    "MCPLaunchError": "launcher",
    "MCPRuntime": "runtime",
    "MCPServerConfig": "config",
    "MCPServerPolicy": "policy",
    "MCPServerStatus": "runtime",
    "MCPTool": "bridge",
    "bridge_mcp_server": "bridge",
    "describe_exception": "client",
    "dump_server_config": "config",
    "load_mcp_config": "config",
    "load_user_mcp_config": "config",
    "normalize_transport": "config",
    "resolve_launch": "launcher",
    "resolve_server_policy": "policy",
    "save_user_mcp_config": "config",
    "start_mcp_runtime": "runtime",
    "synthesize_env": "launcher",
    "tools_from_client": "bridge",
    "user_mcp_path": "config",
}

#: 子模块名。惰性导入子模块本身（``nono_agent.mcp.client``）也走这里。
_SUBMODULES = frozenset({"bridge", "client", "config", "launcher", "policy", "runtime", "runtimes"})

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


def __getattr__(name: str) -> object:
    """PEP 562：首次访问时按需导入，并写回 globals 缓存后续访问。"""
    module = _EXPORTS.get(name)
    if module is not None:
        value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
        globals()[name] = value
        return value
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | _SUBMODULES)
