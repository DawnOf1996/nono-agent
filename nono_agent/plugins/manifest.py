"""Plugin 清单 —— plugin.json 的 schema 与校验。

插件是「分发单元」，只**组合**下层能力（Rules/Skills/Agents/Hooks/MCP/
Tool 声明/权限声明），不实现 Agent 逻辑，无 loop 特权入口。

``tools`` 段是**声明式**的：它只描述「一个 HTTP JSON 端点」「启用一个已声明的
MCP server」或「挂一个引擎内建工具」，由 :mod:`nono_agent.toolplugins` 的内置
执行器解释。清单本身不含可执行代码路径，引擎因此不需要给插件开 Python 导入
的口子 —— ``builtin`` 也不例外：它按名字查一张写死在引擎里的白名单表，清单点
不到表外的任何东西。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ``tools.<name>.kind`` 的可取值。
#
# 前两种是外连（HTTP 端点、MCP server）；``builtin`` 不外连，它把一个**引擎自己
# 写的**工具挂到插件开关下 —— 实现是产品/引擎的一部分，只有「要不要给用户用」
# 交给清单决定。仍然没有 python entrypoint：那种等于让清单指定要 import 什么，
# 是二期的事。
#
# 这份名单与 :data:`nono_agent.toolplugins.spec.KNOWN` 语义相同但各写一份：
# 这里负责「清单本身合不合法」，那边负责「怎么解析成规格」，两边的报错时机和
# 面向对象不同。改一处记得改另一处 —— 有测试盯着两边同步。
KNOWN_TOOL_KINDS = frozenset({"http_json", "mcp", "builtin"})


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
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)  # 工具声明（数据驱动）
    config: dict[str, Any] = field(default_factory=dict)           # 配置面板 schema
    permissions: dict[str, Any] = field(default_factory=dict)     # 权限需求清单
    hooks: dict[str, Any] = field(default_factory=dict)           # hooks.json 内容
    commands: dict[str, dict[str, Any]] = field(default_factory=dict)  # 自定义 slash（数据驱动）
    examples: tuple[str, ...] = ()          # 「可以直接这么问」的示例句，详情面板用

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
            tools=(
                data.get("tools", {}) if isinstance(data.get("tools"), dict) else {}
            ),
            config=data.get("config", {}) if isinstance(data.get("config"), dict) else {},
            permissions=(
                data.get("permissions", {})
                if isinstance(data.get("permissions"), dict)
                else {}
            ),
            hooks=data.get("hooks", {}) if isinstance(data.get("hooks"), dict) else {},
            commands=(
                data.get("commands", {})
                if isinstance(data.get("commands"), dict)
                else {}
            ),
            # 非字符串元素直接丢掉而不是让整个清单作废：examples 只是详情面板上
            # 的展示文案，写错一条不该连带插件用不了。
            examples=tuple(
                item.strip()
                for item in (data.get("examples") or [])
                if isinstance(item, str) and item.strip()
            )
            if isinstance(data.get("examples"), (list, tuple))
            else (),
        )

    def validate(self) -> list[str]:
        """返回校验错误列表（空 = 合法）。"""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if not self.version:
            errors.append("version is required")
        for tool_name in sorted(self.tools):
            spec = self.tools[tool_name]
            if not isinstance(spec, dict):
                errors.append(f"tools.{tool_name} must be an object")
                continue
            kind = str(spec.get("kind", "")).strip()
            if not kind:
                errors.append(f"tools.{tool_name}.kind is required")
            elif kind not in KNOWN_TOOL_KINDS:
                errors.append(
                    f"tools.{tool_name}.kind {kind!r} is not supported "
                    f"(expected one of {sorted(KNOWN_TOOL_KINDS)})"
                )
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
