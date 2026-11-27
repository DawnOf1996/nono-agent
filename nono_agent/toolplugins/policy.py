"""工具插件启停与配置 —— 按插件 id 从 Settings 读取，不进 plugin.json。

与 :mod:`nono_agent.mcp.policy` 同构：**未声明即不启用**（fail-closed）。
用户在 UI 上打开开关等价于写入 ``tools.<id>.enabled = true``。

密钥不进 Settings：这里只保存 ``api_key_env`` 这个**指针**，真实值由
``~/.nono-agent/credentials`` 或同名环境变量提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nono_agent.mcp.policy import as_bool
from nono_agent.settings import Settings


@dataclass(frozen=True)
class ToolPluginPolicy:
    """单个工具插件的策略视图。"""

    enabled: bool = False
    options: dict[str, Any] = field(default_factory=dict)
    api_key_env: str = ""

    @property
    def configured(self) -> bool:
        """没有密钥要求时恒为 True；有要求时由运行时用凭据实际判定。"""
        return not self.api_key_env


def resolve_tool_policy(
    settings: Settings | None,
    plugin_id: str,
    *,
    default_api_key_env: str = "",
) -> ToolPluginPolicy:
    """读取 ``tools.<id>``：未声明则不启用。"""
    block = settings.get(f"tools.{plugin_id}") if settings is not None else None
    if not isinstance(block, dict):
        # 还没写过任何配置，但密钥指针仍然要报出来 —— 设置页得先知道
        # 该让用户填哪个环境变量名，才能谈「配置密钥」。
        return ToolPluginPolicy(api_key_env=default_api_key_env)
    options = block.get("options")
    api_key_env = str(block.get("api_key_env", "")).strip() or default_api_key_env
    return ToolPluginPolicy(
        enabled=as_bool(block.get("enabled"), False),
        options={str(k): v for k, v in options.items()}
        if isinstance(options, dict)
        else {},
        api_key_env=api_key_env,
    )
