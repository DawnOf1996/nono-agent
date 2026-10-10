"""工具插件运行时 —— 发现、鉴权、构建、失败隔离、关闭。

与 :func:`nono_agent.mcp.runtime.start_mcp_runtime` 同构：单个插件起不来
不影响其它插件，错误进状态列表而不是抛出去。

引擎**不自带任何具体插件**：内置插件由产品包在启动时经
:func:`~nono_agent.toolplugins.registry.register_plugin_dir` 注册。
来源优先级 **registered < user < project**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from nono_agent.mcp.config import MCPServerConfig
from nono_agent.mcp.launcher import resolve_launch
from nono_agent.plugins.manifest import PluginManifest, load_manifest
from nono_agent.providers.catalog import lookup_api_key
from nono_agent.settings import Settings, load_settings_from_env
from nono_agent.toolplugins.auth import append_query_auth, header_auth, redact
from nono_agent.toolplugins.http_json import HttpJsonTool
from nono_agent.toolplugins.policy import resolve_tool_policy
from nono_agent.toolplugins.registry import plugin_dirs_in, registered_plugin_dirs
from nono_agent.toolplugins.spec import (
    HttpToolSpec,
    McpToolSpec,
    ToolSpec,
    ToolSpecError,
    parse_tool_specs,
)

logger = logging.getLogger(__name__)


@dataclass
class ToolPluginStatus:
    """单个工具插件的运行时状态，供 ``/plugins`` 与设置页展示。"""

    id: str
    version: str = ""
    description: str = ""
    source: str = "bundled"          # bundled | user | project
    directory: str = ""
    enabled: bool = False
    configured: bool = True          # 密钥是否齐备
    running: bool = False
    api_key_env: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ToolPluginRuntime:
    """一次会话里生效的工具插件集合。"""

    enabled: bool = True
    tools: list[Any] = field(default_factory=list)          # list[Tool]
    clients: list[Any] = field(default_factory=list)         # list[MCPClient]
    statuses: list[ToolPluginStatus] = field(default_factory=list)

    async def close(self) -> None:
        for client in self.clients:
            try:
                await client.close()
            except Exception:
                logger.warning("Failed to close tool plugin MCP client", exc_info=True)
        self.clients.clear()
        self.tools.clear()


# ----------------------------------------------------------------------
# 发现
# ----------------------------------------------------------------------


def builtin_plugin_dirs() -> list[Path]:
    """产品注册进来的内置插件目录。引擎自身不提供任何具体插件。"""
    return [path for root in registered_plugin_dirs() for path in plugin_dirs_in(root)]


def discover_plugins(cwd: str | Path) -> list[tuple[PluginManifest, str]]:
    """发现内置 / 用户级 / 项目级插件，返回 (清单, 来源)。

    同名时后者覆盖前者。**来源靠目录归属判定，不靠路径字符串里有没有
    ``user``** —— ``/home/user/proj`` 这种目录名会把项目插件误报成用户级。
    """
    from nono_agent.paths import project_dir, user_home
    from nono_agent.plugins import FileSystemPluginLoader

    user_dir = user_home() / "plugins"
    project_dir_path = project_dir(cwd) / "plugins"

    out: dict[str, tuple[PluginManifest, str]] = {}
    for directory in builtin_plugin_dirs():
        manifest = load_manifest(directory)
        if manifest is not None:
            out[manifest.name] = (manifest, "bundled")
    for manifest in FileSystemPluginLoader().load(cwd):
        out[manifest.name] = (
            manifest,
            _source_of(manifest.directory, user_dir, project_dir_path),
        )
    return [out[key] for key in sorted(out)]


def _source_of(directory: Path, user_dir: Path, project_dir_path: Path) -> str:
    """按目录归属判定来源，项目级优先于用户级。"""
    resolved = directory.resolve()
    try:
        resolved.relative_to(project_dir_path.resolve())
        return "project"
    except ValueError:
        pass
    try:
        resolved.relative_to(user_dir.resolve())
        return "user"
    except ValueError:
        return "project"


# ----------------------------------------------------------------------
# 运行
# ----------------------------------------------------------------------


async def start_tool_plugin_runtime(
    cwd: str | Path,
    *,
    settings: Settings | None = None,
    data_dir: Path | None = None,
) -> ToolPluginRuntime:
    """按设置构建工具插件工具。单个插件失败不影响其它。"""
    resolved = settings if settings is not None else load_settings_from_env(cwd)
    if not bool(resolved.get("tools.enabled", True)):
        return ToolPluginRuntime(enabled=False)

    workspace = Path(cwd)
    external_servers = _external_mcp_servers(cwd)
    tools: list[Any] = []
    clients: list[Any] = []
    statuses: list[ToolPluginStatus] = []

    for manifest, source in discover_plugins(workspace):
        status = ToolPluginStatus(
            id=manifest.name,
            version=manifest.version,
            description=manifest.description,
            source=source,
            directory=str(manifest.directory),
        )
        if not manifest.tools:
            continue

        declared_env = api_key_env_for(manifest)
        policy = resolve_tool_policy(
            resolved, manifest.name, default_api_key_env=declared_env
        )
        # settings 里的 api_key_env 覆盖清单里的指针 —— 取值与查凭据必须用
        # 同一个名字，否则用户改了指针却仍在读旧的那把钥匙。
        api_key_env = policy.api_key_env
        status.api_key_env = api_key_env
        status.enabled = policy.enabled
        status.options = dict(policy.options)
        if not policy.enabled:
            statuses.append(status)
            continue

        api_key = lookup_api_key(api_key_env) if api_key_env else ""
        status.configured = not api_key_env or bool(api_key)

        try:
            specs = parse_tool_specs(manifest.name, manifest.tools)
        except ToolSpecError as exc:
            status.error = f"invalid declaration: {exc}"
            statuses.append(status)
            continue

        if api_key_env and api_key_env != declared_env:
            # settings 覆盖了密钥指针：把规格里的指针一并改掉，否则工具
            # 报「缺 $清单里的名字」，而用户填的是另一个名字。
            specs = [_with_api_key_env(spec, api_key_env) for spec in specs]

        for spec in specs:
            try:
                built, started = await _build_tool(
                    spec, manifest, policy, api_key, workspace, data_dir, external_servers
                )
            except Exception as exc:  # 单条工具失败不影响同插件其它工具
                # query 鉴权时 Key 在 URL 里，而 MCP 的异常消息会带上 URL ——
                # 不抹掉就会顺着 status.error 冒到界面和接口上。
                detail = redact(str(exc), api_key)
                logger.warning("Tool plugin %s/%s failed: %s", manifest.name, spec.name, detail)
                logger.debug(
                    "Tool plugin %s/%s traceback", manifest.name, spec.name, exc_info=True
                )
                status.error = detail
                continue
            tools.extend(built)
            clients.extend(started)
            status.tools.extend(getattr(t, "name", "") for t in built)
            status.running = status.running or bool(built)

        statuses.append(status)

    return ToolPluginRuntime(
        enabled=True, tools=tools, clients=clients, statuses=statuses
    )


async def _build_tool(
    spec: ToolSpec,
    manifest: PluginManifest,
    policy: Any,
    api_key: str,
    workspace: Path,
    data_dir: Path | None,
    external_servers: set[str],
) -> tuple[list[Any], list[Any]]:
    if isinstance(spec, HttpToolSpec):
        return [HttpJsonTool(spec, policy.options, api_key)], []
    return await _build_mcp_tool(
        spec, manifest, policy, api_key, workspace, data_dir, external_servers
    )


async def _build_mcp_tool(
    spec: McpToolSpec,
    manifest: PluginManifest,
    policy: Any,
    api_key: str,
    workspace: Path,
    data_dir: Path | None,
    external_servers: set[str],
) -> tuple[list[Any], list[Any]]:
    """启用插件已声明的 MCP server，把它的工具按白名单接进来。"""
    # 官方 MCP SDK 推迟到这里导入：本模块在 sidecar 启动链上（register_builtin_plugins），
    # 但只有真的要拉起 MCP server 时才需要 SDK。
    from nono_agent.mcp.bridge import tools_from_client
    from nono_agent.mcp.client import MCPClient

    raw = manifest.mcp.get(spec.server)
    if not isinstance(raw, dict):
        raise RuntimeError(f"plugin declares no mcp server named '{spec.server}'")

    # 只把**别的**来源当作冲突：用户/其它插件已经在配这个 server 才让位。
    # 插件自己 mcp 段里声明的 server 必须排除掉 —— collect_mcp_servers 读的
    # 就是同一份清单，不排除的话任何用户级插件的 kind=mcp 都起不来。
    if spec.server in external_servers - set(manifest.mcp):
        raise RuntimeError(
            f"MCP server '{spec.server}' is already configured by the user; "
            "the bundled preset is skipped."
        )

    # options.server 覆盖清单里的连接字段（用户想换 transport / 换镜像端点时用）
    options = {**spec.defaults, **policy.options}
    merged = {**raw, **_as_dict(options.get("server"))}
    config = MCPServerConfig.from_dict(spec.server, merged)

    if spec.auth.needs_api_key:
        if not api_key:
            raise RuntimeError(
                f"missing API key for ${spec.auth.api_key_env}; "
                "set it in the settings panel to enable this tool."
            )
        if spec.auth.type == "query":
            if not spec.auth.query_param:
                raise RuntimeError("auth type=query requires 'query_param'")
            config.url = append_query_auth(config.url or "", spec.auth.query_param, api_key)
        else:
            config.headers = {
                **config.headers,
                **header_auth(
                    spec.auth.type, spec.auth.header, spec.auth.prefix, api_key
                ),
            }

    recipe = resolve_launch(config, workspace, data_dir=data_dir)
    client = MCPClient(config, recipe=recipe, workspace=workspace, data_dir=data_dir)
    await client.start()
    try:
        bridged = await tools_from_client(client, config.name)
    except Exception:
        await client.close()
        raise

    if spec.allowed_tools:
        allowed = set(spec.allowed_tools)
        bridged = [t for t in bridged if t.mcp_tool_name in allowed]
    return list(bridged), [client]


def option_defaults(manifest: PluginManifest) -> dict[str, Any]:
    """汇总插件所有工具声明的 ``defaults`` —— 配置面板回填默认值用。

    同名键取值必须一致；不一致说明清单写错了，宁可取第一个也不要猜。
    """
    merged: dict[str, Any] = {}
    for entry in manifest.tools.values():
        if not isinstance(entry, dict):
            continue
        defaults = entry.get("defaults")
        if not isinstance(defaults, dict):
            continue
        for key, value in defaults.items():
            merged.setdefault(str(key), value)
    return merged


def _with_api_key_env(spec: ToolSpec, api_key_env: str) -> ToolSpec:
    """复制一份规格并改写密钥指针（AuthSpec 是 frozen 的）。"""
    return replace(spec, auth=replace(spec.auth, api_key_env=api_key_env))


def api_key_env_for(manifest: PluginManifest) -> str:
    """从 tools 段里取唯一的密钥指针（多个条目必须一致，否则不可用）。"""
    envs = {
        str(entry.get("auth", {}).get("api_key_env", "")).strip()
        for entry in manifest.tools.values()
        if isinstance(entry, dict)
    }
    envs.discard("")
    return envs.pop() if len(envs) == 1 else ""


def _external_mcp_servers(cwd: str | Path) -> set[str]:
    """用户/项目已配置的 MCP server 名 —— 内置预设不得覆盖用户显式配置。"""
    try:
        from nono_agent.mcp.runtime import collect_mcp_servers

        return set(collect_mcp_servers(cwd))
    except Exception:
        logger.warning("Failed to resolve external MCP servers", exc_info=True)
        return set()


def _as_dict(value: Any) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}
