"""MCP 启动配方 —— HTTP 由产品连接；stdio 把 python/uv/npm 等接到内置运行时。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nono_agent.mcp.config import MCPServerConfig
from nono_agent.mcp.runtimes import apply_hosted_env, rewrite_stdio_command
from nono_agent.paths import user_home


class MCPLaunchError(ValueError):
    """启动配方无法解析。"""


@dataclass
class LaunchRecipe:
    """已解析的启动配方。"""

    transport: str
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    stderr_log: Path | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def mcp_data_dir() -> Path:
    """用户级 MCP 数据根目录：``~/.nono-agent/mcp``（日志等）。"""
    return user_home() / "mcp"


def synthesize_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """SDK 默认宿主环境 + 用户在清单里声明的变量。"""
    # 只有真要拉起 stdio server 时才需要官方 SDK 的宿主环境计算，放在函数内导入：
    # 模块级导入会把整个 mcp SDK 拖进 sidecar 的启动链（实测约 630ms），
    # 而 sidecar 启动只需要这里的一个纯函数。
    from mcp.client.stdio import get_default_environment

    env = get_default_environment()
    if extra:
        env.update(extra)
    return env


def resolve_launch(
    config: MCPServerConfig,
    workspace: str | Path,
    *,
    data_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> LaunchRecipe:
    """把清单项收成可执行配方。"""
    workspace_path = Path(workspace).resolve()
    transport = config.normalized_transport
    if transport in {"http", "sse"}:
        return _resolve_remote(config, transport)
    return _resolve_stdio(
        config, workspace_path, data_dir=data_dir, runtime_root=runtime_root
    )


def _resolve_remote(config: MCPServerConfig, transport: str) -> LaunchRecipe:
    if not config.url:
        raise MCPLaunchError(
            f"MCP server {config.name!r} transport={transport} requires a url."
        )
    return LaunchRecipe(
        transport=transport, url=config.url, headers=dict(config.headers)
    )


def _resolve_stdio(
    config: MCPServerConfig,
    workspace: Path,
    *,
    data_dir: Path | None,
    runtime_root: Path | None,
) -> LaunchRecipe:
    argv, hosted = _stdio_argv(config, workspace, runtime_root=runtime_root)
    file_env = _load_env_file(config.env_file, workspace)
    env = synthesize_env({**file_env, **(config.env or {})} or None)
    if hosted:
        env = apply_hosted_env(env, runtime_root)

    root = data_dir if data_dir is not None else mcp_data_dir()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cwd = config.cwd or str(workspace)
    return LaunchRecipe(
        transport="stdio",
        argv=argv,
        env=env,
        cwd=cwd,
        stderr_log=log_dir / f"{config.name}.stderr.log",
    )


def _load_env_file(env_file: str | None, workspace: Path) -> dict[str, str]:
    if not env_file:
        return {}
    path = Path(env_file)
    if not path.is_absolute():
        path = workspace / path
    if not path.is_file():
        return {}
    loaded: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            loaded[key] = value.strip().strip("'").strip('"')
    return loaded


def _stdio_argv(
    config: MCPServerConfig,
    workspace: Path,
    *,
    runtime_root: Path | None,
) -> tuple[list[str], bool]:
    if config.command:
        argv = [config.command, *config.args]
    elif config.runtime and config.entrypoint:
        entry = Path(config.entrypoint)
        if not entry.is_absolute():
            entry = workspace / entry
        runtime = config.runtime.strip().lower()
        if runtime == "python":
            argv = ["python", "-u", str(entry)]
        elif runtime == "node":
            argv = ["node", str(entry)]
        else:
            raise MCPLaunchError(
                f"MCP server {config.name!r} has unsupported runtime {config.runtime!r}."
            )
    else:
        raise MCPLaunchError(
            f"MCP server {config.name!r} requires command or runtime+entrypoint."
        )
    bound, hosted = rewrite_stdio_command(argv[0], root=runtime_root)
    argv[0] = bound
    return argv, hosted
