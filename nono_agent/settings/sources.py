"""Settings 来源 —— default / user / project / policy / flag 读取与合并。

优先级（低 → 高）：default < user < project < policy < flag/env。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nono_agent.paths import CONFIG_DIR_NAME, is_data_dir, project_dir, user_home
from nono_agent.settings.merge import deep_merge
from nono_agent.settings.models import Settings

# 默认引擎内置设置（后续可扩展）
DEFAULT_SETTINGS: dict[str, Any] = {
    "permissions": {"mode": "default", "rules": []},
    "mcp": {"enabled": True, "servers": {}},
    # 工具插件：tools.<plugin-id>.{enabled,api_key_env,options}。
    # 未声明即不启用 —— 打开开关由产品层写入，引擎只读。
    "tools": {"enabled": True, "plugins": {}},
}

_PROJECT_SETTINGS_REL = Path(CONFIG_DIR_NAME) / "settings.json"


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件 —— 不存在或损坏返回空字典。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_user_settings(path: Path | None = None) -> dict[str, Any]:
    """加载用户级设置（``~/.nono-agent/settings.json``）。"""
    return _read_json(path or user_settings_path())


def project_settings_path(cwd: str | Path) -> Path:
    """工作区设置文件：``<cwd>/.nono-agent/settings.json``。"""
    return project_dir(cwd) / "settings.json"


def patch_user_settings(patch: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """把补丁深合并进用户级 ``settings.json`` 并写回。"""
    dest = path or user_settings_path()
    current = _read_json(dest)
    merged = deep_merge(current, patch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def user_settings_path() -> Path:
    """用户级设置文件：``~/.nono-agent/settings.json``。"""
    return user_home() / "settings.json"


def load_project_settings(cwd: str | Path) -> dict[str, Any]:
    """加载项目级设置（``.nono-agent/settings.json``，从 cwd 向上查找第一个）。

    **数据目录不能被当成项目级的。** cwd 在数据目录下面时（比如
    ``~/projects/foo``，数据目录在默认的 ``~/.nono-agent``），向上查找会撞上
    那份 ``settings.json`` —— 同一个文件已经在 user 层读过一次了，再当 project
    层读一遍会让优先级凭空多出一层，``source_of()`` 也会把 user 层的键误报成
    project。用 :func:`is_data_dir` 判定，数据目录搬过家之后同样生效。

    同时**不越过家目录**：家目录之上（``C:\\Users``、``C:\\``）几乎不可能是
    真正的项目根，继续往上只会捡到别的用户或整机的配置。
    """
    current = Path(cwd).resolve()
    # 边界取数据目录的父目录（而不是 default_user_home）：数据目录搬到哪儿，
    # 它的父目录就是那条「之上不算项目根」的分界。数据目录本身由
    # is_data_dir 单独挡 —— 搬过家之后老位置那一份也得挡住。
    home = user_home().parent.resolve()
    while True:
        candidate = current / _PROJECT_SETTINGS_REL
        if candidate.exists() and not is_data_dir(candidate.parent):
            data = _read_json(candidate)
            if data:
                return data
        if current.parent == current or current == home:
            break
        current = current.parent
    return {}


def patch_project_settings(cwd: str | Path, patch: dict[str, Any]) -> dict[str, Any]:
    """把补丁深合并进工作区 ``settings.json`` 并写回。"""
    path = project_settings_path(cwd)
    current = _read_json(path)
    merged = deep_merge(current, patch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def load_settings(
    cwd: str | Path,
    *,
    user_settings: dict[str, Any] | None = None,
    project_settings: dict[str, Any] | None = None,
    policy_settings: dict[str, Any] | None = None,
    flag_settings: dict[str, Any] | None = None,
) -> Settings:
    """加载并按优先级合并所有来源，返回带来源追踪的 Settings。

    优先级：default < user < project < policy < flag。
    """
    layers: list[tuple[str, dict[str, Any]]] = [
        ("default", DEFAULT_SETTINGS),
        ("user", user_settings if user_settings is not None else load_user_settings()),
        (
            "project",
            project_settings if project_settings is not None else load_project_settings(cwd),
        ),
        ("policy", policy_settings or {}),
        ("flag", flag_settings or {}),
    ]

    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for source, layer in layers:
        merged = deep_merge(merged, layer)
        _record_sources(sources, layer, source)

    return Settings(merged, sources)


def _record_sources(
    sources: dict[str, str],
    layer: dict[str, Any],
    source: str,
    prefix: str = "",
) -> None:
    """递归记录每片叶子值的来源（点分路径）。"""
    for key, value in layer.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            _record_sources(sources, value, source, prefix=full_key)
        else:
            sources[full_key] = source


def load_settings_from_env(cwd: str | Path) -> Settings:
    """从环境变量加载 flag 层（NONO_*）并合并。

    支持的 flag：``NONO_PERMISSION_MODE`` → permissions.mode；
    ``NONO_MCP_ENABLED`` → mcp.enabled。
    单 server 启停 / 工具禁用见 ``mcp.servers.<id>``，不走环境变量。
    """
    flags: dict[str, Any] = {}
    mode = os.environ.get("NONO_PERMISSION_MODE")
    if mode:
        flags.setdefault("permissions", {})["mode"] = mode
    mcp_enabled = os.environ.get("NONO_MCP_ENABLED")
    if mcp_enabled is not None:
        flags.setdefault("mcp", {})["enabled"] = mcp_enabled.strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    return load_settings(cwd, flag_settings=flags)
