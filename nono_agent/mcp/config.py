"""MCP 服务器配置 —— mcp.json 清单与 stdio/SSE 传输参数。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPServerConfig:
    """单个 MCP 服务器配置。"""

    name: str                                   # 唯一名称（前缀工具名）
    command: str | None = None                  # stdio: 启动命令
    args: list[str] = field(default_factory=list)   # stdio: 命令参数
    env: dict[str, str] = field(default_factory=dict)  # stdio: 环境变量
    transport: str = "stdio"                    # stdio | sse
    url: str | None = None                      # sse: 端点 URL
    enabled: bool = True                        # 是否启用
    token: str | None = None                    # 静态令牌（stdio 经 env 传递 / sse 经 header）
    headers: dict[str, str] = field(default_factory=dict)  # sse: 额外 HTTP 头

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> MCPServerConfig:
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
            transport=str(data.get("transport", "stdio")),
            url=data.get("url"),
            enabled=bool(data.get("enabled", True)),
            token=data.get("token"),
            headers=(
                {str(k): str(v) for k, v in data.get("headers", {}).items()}
                if isinstance(data.get("headers"), dict)
                else {}
            ),
        )


def load_mcp_config(path: str | Path) -> dict[str, MCPServerConfig]:
    """从 mcp.json 加载服务器配置。文件不存在/损坏返回空字典。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    servers_raw = data.get("servers", data) if isinstance(data, dict) else {}
    servers: dict[str, MCPServerConfig] = {}
    if isinstance(servers_raw, dict):
        for name, server_data in servers_raw.items():
            if isinstance(server_data, dict):
                servers[name] = MCPServerConfig.from_dict(name, server_data)
    return servers
