"""用户级 MCP 清单的读写，以及按工作区启停。"""

from __future__ import annotations

import re
from typing import Any

from nono_agent.mcp.config import (
    MCPServerConfig,
    load_user_mcp_config,
    user_mcp_path,
)
from nono_agent.mcp.policy import (
    as_bool,
    resolve_server_policy,
)
from nono_agent.mcp.runtime import collect_mcp_servers
from nono_agent.paths import resolve_session_cwd
from nono_agent.settings import load_settings_from_env
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.mcp.schemas import McpServerUpsert

_SERVER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _validate_server_id(server_id: str) -> str:
    sid = server_id.strip()
    if not _SERVER_ID.match(sid):
        raise ValueError("id 需为小写字母开头，只能含小写字母、数字、下划线和连字符")
    return sid


def _config_from_body(body: McpServerUpsert) -> MCPServerConfig:
    sid = _validate_server_id(body.id)
    transport = (body.transport or "stdio").strip().lower()
    command = (body.command or "").strip() or None
    url = (body.url or "").strip() or None
    if transport in {"http", "sse"}:
        if not url:
            raise ValueError("远程 MCP 需要填写 url")
    elif not command:
        raise ValueError("stdio MCP 需要填写 command")
    return MCPServerConfig(
        name=sid,
        command=command,
        args=[str(item) for item in body.args if str(item).strip()],
        env={str(key): str(value) for key, value in body.env.items() if str(key).strip()},
        transport=transport,
        url=url,
        headers={str(key): str(value) for key, value in body.headers.items() if str(key).strip()},
        env_file=(body.env_file or "").strip() or None,
        cwd=(body.cwd or "").strip() or None,
    )


def _live_statuses(cwd: str, manager: SessionManager) -> dict[str, Any]:
    resolved = resolve_session_cwd(cwd)
    for engine in manager.sessions.values():
        if resolve_session_cwd(engine._config.cwd) != resolved:
            continue
        runtime = engine.mcp_runtime
        if runtime is None:
            continue
        return {status.name: status for status in runtime.statuses}
    return {}


def public_mcp(cwd: str, manager: SessionManager) -> dict[str, Any]:
    raw_cwd = (cwd or "").strip()
    settings = load_settings_from_env(raw_cwd) if raw_cwd else None
    user_servers = load_user_mcp_config()
    servers = collect_mcp_servers(raw_cwd)
    live = _live_statuses(raw_cwd, manager) if raw_cwd else {}
    items: list[dict[str, Any]] = []
    for name, cfg in servers.items():
        policy = resolve_server_policy(settings, name)
        status = live.get(name)
        items.append(
            {
                "id": name,
                "transport": cfg.normalized_transport,
                "command": cfg.command,
                "args": list(cfg.args),
                "env": dict(cfg.env),
                "url": cfg.url,
                "headers": dict(cfg.headers),
                "env_file": cfg.env_file,
                "cwd": cfg.cwd,
                "source": "user" if name in user_servers else "plugin",
                "enabled": policy.enabled,
                "running": bool(getattr(status, "running", False)),
                "tool_count": int(getattr(status, "tool_count", 0) or 0),
                "error": getattr(status, "error", None),
                "disabled_tools": sorted(policy.disabled_tools),
            }
        )
    return {
        "config_path": str(user_mcp_path()),
        "enabled": as_bool(settings.get("mcp.enabled", True) if settings else True),
        "servers": items,
    }
