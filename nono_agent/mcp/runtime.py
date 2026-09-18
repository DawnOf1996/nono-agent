"""工作区 MCP 运行时 —— 读清单、启动、失败隔离、关闭。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from nono_agent.mcp.bridge import MCPTool, tools_from_client
from nono_agent.mcp.client import MCPClient, describe_exception
from nono_agent.mcp.config import MCPServerConfig, load_mcp_config, user_mcp_path
from nono_agent.mcp.launcher import resolve_launch
from nono_agent.mcp.policy import as_bool, resolve_server_policy
from nono_agent.settings import Settings, load_settings_from_env

logger = logging.getLogger(__name__)


@dataclass
class MCPServerStatus:
    """单个 server 的运行时状态，供 /mcp 展示。"""

    name: str
    transport: str
    enabled: bool
    running: bool
    tool_count: int = 0
    error: str | None = None
    command: str | None = None
    url: str | None = None
    disabled_tools: list[str] = field(default_factory=list)
    source: str = "user"


@dataclass
class MCPRuntime:
    """一次会话里拉起的 MCP 连接集合。"""

    enabled: bool
    tools: list[MCPTool] = field(default_factory=list)
    clients: list[MCPClient] = field(default_factory=list)
    statuses: list[MCPServerStatus] = field(default_factory=list)

    async def close(self) -> None:
        for client in self.clients:
            try:
                await client.close()
            except Exception:
                logger.warning("Failed to close MCP client", exc_info=True)
        self.clients.clear()
        self.tools.clear()


def collect_mcp_servers(
    cwd: str | Path,
    *,
    mcp_path: str | Path | None = None,
) -> dict[str, MCPServerConfig]:
    """用户级 mcp.json ∪ 工作区插件声明；同名以用户清单为准。"""
    servers = dict(load_mcp_config(mcp_path if mcp_path is not None else user_mcp_path()))
    if not str(cwd).strip():
        return servers
    try:
        from nono_agent.plugins import FileSystemPluginLoader, activate_mcp

        for manifest in FileSystemPluginLoader().load(cwd):
            for name, cfg in activate_mcp(manifest).items():
                servers.setdefault(name, cfg)
    except Exception:
        logger.warning("Failed to load plugin MCP servers", exc_info=True)
    return servers


async def start_mcp_runtime(
    cwd: str | Path,
    *,
    settings: Settings | None = None,
    data_dir: Path | None = None,
    mcp_path: str | Path | None = None,
) -> MCPRuntime:
    """按设置启动工作区 MCP。单个 server 失败不影响其它。"""
    resolved = settings if settings is not None else load_settings_from_env(cwd)
    if not as_bool(resolved.get("mcp.enabled", True)):
        return MCPRuntime(enabled=False)

    catalog_path = mcp_path if mcp_path is not None else user_mcp_path()
    user_ids = set(load_mcp_config(catalog_path))
    servers = collect_mcp_servers(cwd, mcp_path=catalog_path)
    if not servers:
        return MCPRuntime(enabled=True)

    workspace = Path(cwd)
    tools: list[MCPTool] = []
    clients: list[MCPClient] = []
    statuses: list[MCPServerStatus] = []

    for name, cfg in servers.items():
        transport = cfg.normalized_transport
        policy = resolve_server_policy(resolved, name)
        base = MCPServerStatus(
            name=name,
            transport=transport,
            enabled=policy.enabled,
            running=False,
            command=cfg.command,
            url=cfg.url,
            disabled_tools=sorted(policy.disabled_tools),
            source="user" if name in user_ids else "plugin",
        )
        if not policy.enabled:
            statuses.append(base)
            continue
        try:
            recipe = resolve_launch(cfg, workspace, data_dir=data_dir)
            client = MCPClient(cfg, recipe=recipe, workspace=workspace, data_dir=data_dir)
            await client.start()
            bridged = await tools_from_client(
                client, cfg.name, disabled_tools=policy.disabled_tools
            )
            tools.extend(bridged)
            clients.append(client)
            base.running = True
            base.tool_count = len(bridged)
            if recipe.argv:
                base.command = recipe.argv[0]
            if recipe.url:
                base.url = recipe.url
        except Exception as exc:
            detail = describe_exception(exc)
            logger.warning("MCP server %s failed to start: %s", name, detail)
            logger.debug("MCP server %s startup traceback", name, exc_info=True)
            base.error = detail
        statuses.append(base)

    return MCPRuntime(
        enabled=True,
        tools=tools,
        clients=clients,
        statuses=statuses,
    )
