"""工具插件设置的请求体。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolPluginUpsert(BaseModel):
    """开关与非密配置。**没有 api_key 字段** —— 密钥走单独的端点。"""

    cwd: str = ""
    enabled: bool | None = None
    options: dict[str, Any] | None = None


class ToolPluginApiKey(BaseModel):
    """写入凭据；``api_key=""`` 表示清除。响应永不回显密钥。"""

    cwd: str = ""
    api_key: str = ""
