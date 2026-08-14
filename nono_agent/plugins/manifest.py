"""Plugin 清单 —— plugin.json 的 schema 与校验。

插件是「分发单元」，只**组合**下层能力（Rules/Skills/Agents/Hooks/MCP/
权限声明），不实现具体 Tool/Agent 逻辑，无 loop 特权入口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PluginManifest:
    """插件清单。"""

    name: str
    version: str
    description: str = ""
    directory: Path = field(default_factory=Path)  # 插件根目录
    rules_dir: str = "rules"                       # 相对路径：规则目录
    skills_dir: str = "skills"                     # 相对路径：技能目录
    agents_dir: str = "agents"                     # 相对路径：子 Agent 目录
    mcp: dict[str, dict[str, Any]] = field(default_factory=dict)  # mcp server 声明
    permissions: dict[str, Any] = field(default_factory=dict)     # 权限需求清单
    hooks: dict[str, Any] = field(default_factory=dict)           # hooks.json 内容

    @classmethod
    def from_dict(cls, data: dict[str, Any], directory: Path) -> PluginManifest:
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.0.0")),
            description=str(data.get("description", "")),
            directory=directory,
            rules_dir=str(data.get("rules_dir", "rules")),
            skills_dir=str(data.get("skills_dir", "skills")),
            agents_dir=str(data.get("agents_dir", "agents")),
            mcp=data.get("mcp", {}) if isinstance(data.get("mcp"), dict) else {},
            permissions=(
                data.get("permissions", {})
                if isinstance(data.get("permissions"), dict)
                else {}
            ),
            hooks=data.get("hooks", {}) if isinstance(data.get("hooks"), dict) else {},
        )

    def validate(self) -> list[str]:
        """返回校验错误列表（空 = 合法）。"""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if not self.version:
            errors.append("version is required")
        return errors


def load_manifest(directory: Path) -> PluginManifest | None:
    """从插件目录读取 plugin.json；不存在/损坏返回 None。"""
    manifest_file = directory / "plugin.json"
    if not manifest_file.is_file():
        return None
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    manifest = PluginManifest.from_dict(data, directory)
    if manifest.validate():
        return None
    return manifest
