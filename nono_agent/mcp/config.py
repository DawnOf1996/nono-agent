"""MCP 服务器清单 —— 只解析连接字段（对齐 Cursor mcp.json）。

自定义服务定义只写用户级 ``~/.nono-agent/mcp.json``。
工作区启停与工具禁用见 ``settings.mcp.servers.<id>``，不写在本文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HTTP_ALIASES = {"http", "streamable-http", "streamable_http", "streamablehttp"}


def normalize_transport(value: str | None) -> str:
    """归一化传输名：``http`` / ``sse`` / ``stdio``。"""
    raw = (value or "stdio").strip().lower().replace("_", "-")
    if raw in _HTTP_ALIASES:
        return "http"
    if raw == "sse":
        return "sse"
    return "stdio"


def _infer_transport(data: dict[str, Any]) -> str:
    raw = data.get("type", data.get("transport"))
    if raw:
        return str(raw)
    if data.get("url") and not data.get("command"):
        return "http"
    return "stdio"


@dataclass
class MCPServerConfig:
    """单个 MCP 服务器连接配置。"""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    env_file: str | None = None
    runtime: str | None = None
    entrypoint: str | None = None
    requirements: str | None = None
    cwd: str | None = None

    @property
    def normalized_transport(self) -> str:
        return normalize_transport(self.transport)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> MCPServerConfig:
        auth = data.get("auth")
        return cls(
            name=name,
            command=data.get("command"),
            args=(
                [str(a) for a in data.get("args", [])]
                if isinstance(data.get("args"), list)
                else []
            ),
            env=(
                {str(k): str(v) for k, v in data.get("env", {}).items()}
                if isinstance(data.get("env"), dict)
                else {}
            ),
            transport=_infer_transport(data),
            url=data.get("url"),
            headers=(
                {str(k): str(v) for k, v in data.get("headers", {}).items()}
                if isinstance(data.get("headers"), dict)
                else {}
            ),
            auth=(
                {str(k): v for k, v in auth.items()}
                if isinstance(auth, dict)
                else {}
            ),
            env_file=str(data["envFile"]) if data.get("envFile") else None,
            runtime=str(data["runtime"]) if data.get("runtime") else None,
            entrypoint=str(data["entrypoint"]) if data.get("entrypoint") else None,
            requirements=str(data["requirements"]) if data.get("requirements") else None,
            cwd=str(data["cwd"]) if data.get("cwd") else None,
        )


def user_mcp_path() -> Path:
    """用户级 MCP 连接清单：``~/.nono-agent/mcp.json``。"""
    from nono_agent.paths import user_home

    return user_home() / "mcp.json"


def dump_server_config(config: MCPServerConfig) -> dict[str, Any]:
    """把连接配置写成 Cursor 同款 mcp.json 字段。"""
    data: dict[str, Any] = {"type": config.normalized_transport}
    if config.command:
        data["command"] = config.command
    if config.args:
        data["args"] = list(config.args)
    if config.env:
        data["env"] = dict(config.env)
    if config.url:
        data["url"] = config.url
    if config.headers:
        data["headers"] = dict(config.headers)
    if config.auth:
        data["auth"] = dict(config.auth)
    if config.env_file:
        data["envFile"] = config.env_file
    if config.runtime:
        data["runtime"] = config.runtime
    if config.entrypoint:
        data["entrypoint"] = config.entrypoint
    if config.requirements:
        data["requirements"] = config.requirements
    if config.cwd:
        data["cwd"] = config.cwd
    return data


def save_mcp_config(path: str | Path, servers: dict[str, MCPServerConfig]) -> Path:
    """把连接清单写入 mcp.json（只含连接字段）。"""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcpServers": {
            name: dump_server_config(config) for name, config in servers.items()
        }
    }
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


def load_user_mcp_config() -> dict[str, MCPServerConfig]:
    """加载用户级自定义 MCP 服务定义。"""
    return load_mcp_config(user_mcp_path())


def save_user_mcp_config(servers: dict[str, MCPServerConfig]) -> Path:
    """覆盖写入用户级 ``mcp.json``。"""
    return save_mcp_config(user_mcp_path(), servers)


def load_mcp_config(path: str | Path) -> dict[str, MCPServerConfig]:
    """从 mcp.json 加载服务器连接配置。文件不存在/损坏返回空字典。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}
    servers_raw = data.get("mcpServers", data.get("servers", data))
    servers: dict[str, MCPServerConfig] = {}
    if isinstance(servers_raw, dict):
        for name, server_data in servers_raw.items():
            if isinstance(server_data, dict):
                servers[name] = MCPServerConfig.from_dict(name, server_data)
    return servers
