"""Settings 来源 —— default / user / project / policy / flag 读取与合并。

优先级（低 → 高）：default < user < project < policy < flag/env。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nono_agent.settings.merge import deep_merge
from nono_agent.settings.models import Settings

# 默认引擎内置设置（后续可扩展）
DEFAULT_SETTINGS: dict[str, Any] = {
    "permissions": {"mode": "default", "rules": []},
}

_USER_SETTINGS_PATH = Path.home() / ".nono" / "settings.json"
_PROJECT_SETTINGS_PATH = Path(".nono") / "settings.json"


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
    """加载用户级设置（``~/.nono/settings.json``）。"""
    return _read_json(path or _USER_SETTINGS_PATH)


def load_project_settings(cwd: str | Path) -> dict[str, Any]:
    """加载项目级设置（``.nono/settings.json``，从 cwd 向上查找第一个）。"""
    current = Path(cwd).resolve()
    while True:
        candidate = current / _PROJECT_SETTINGS_PATH
        if candidate.exists():
            data = _read_json(candidate)
            if data:
                return data
        if current.parent == current:
            break
        current = current.parent
    return {}


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

    支持的 flag：``NONO_PERMISSION_MODE`` → permissions.mode。
    """
    flags: dict[str, Any] = {}
    mode = os.environ.get("NONO_PERMISSION_MODE")
    if mode:
        flags.setdefault("permissions", {})["mode"] = mode
    return load_settings(cwd, flag_settings=flags)
