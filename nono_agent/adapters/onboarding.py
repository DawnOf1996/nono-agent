"""CLI 引导配置 —— 用户级基础配置（模型 / 协议 / 密钥）的读取与判定。

用户级基础配置保存在 ``~/.nono-agent/config.json``：

.. code-block:: json

    {
        "model": "MiniMax-M3",
        "provider": {
            "adapter": "openai",
            "api_key": "sk-...",
            "base_url": "https://api.minimaxi.com/v1"
        }
    }

CLI 启动时据此判断是否已具备「必备配置」（协议 + 密钥），
缺失时进入交互式引导。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nono_agent.paths import user_home

# 用户级基础配置文件路径
USER_CONFIG_PATH = user_home() / "config.json"

# 环境变量（与 cli 的 Provider 解析一致）
ENV_MODEL = "NONO_MODEL"
ENV_ADAPTER = "NONO_ADAPTER"
ENV_API_KEY = "NONO_API_KEY"
ENV_BASE_URL = "NONO_BASE_URL"

# 支持的协议（adapter）清单
ADAPTER_CHOICES = [
    "openai",
    "anthropic",
    "messages_compatible",
    "responses",
    "responses_compatible",
]


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件 —— 不存在/损坏返回空字典。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_user_config() -> dict[str, Any]:
    """加载用户级基础配置（~/.nono-agent/config.json）。"""
    return _read_json(USER_CONFIG_PATH)


def save_user_config(config: dict[str, Any]) -> None:
    """写入用户级基础配置。"""
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _provider_fields(cfg: dict[str, Any]) -> dict[str, Any]:
    """从配置字典提取 provider 字段（支持顶层与 provider 段两种写法）。"""
    provider = cfg.get("provider")
    if not isinstance(provider, dict):
        provider = {}
    return {
        "adapter": provider.get("adapter") or cfg.get("adapter"),
        "api_key": provider.get("api_key") or cfg.get("api_key"),
        "base_url": provider.get("base_url") or cfg.get("base_url"),
    }


def resolve_provider_fields(config_file: str | None = None) -> dict[str, Any]:
    """合并 provider 字段，优先级：环境变量 > --config 文件 > 用户配置。"""
    file_cfg = _read_json(Path(config_file)) if config_file else {}
    user_fields = _provider_fields(load_user_config())
    file_fields = _provider_fields(file_cfg)

    return {
        "adapter": (
            os.environ.get(ENV_ADAPTER)
            or file_fields["adapter"]
            or user_fields["adapter"]
        ),
        "api_key": (
            os.environ.get(ENV_API_KEY)
            or file_fields["api_key"]
            or user_fields["api_key"]
        ),
        "base_url": (
            os.environ.get(ENV_BASE_URL)
            or file_fields["base_url"]
            or user_fields["base_url"]
        ),
    }


def is_base_configured(config_file: str | None = None) -> bool:
    """判断是否已具备「必备配置」（协议 + 密钥）。"""
    fields = resolve_provider_fields(config_file)
    return bool(fields["adapter"]) and bool(fields["api_key"])
