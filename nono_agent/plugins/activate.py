"""插件激活 —— 把插件清单组合注册到各 Registry/Loader 的产物。

插件无 loop 特权入口：只产出 Rule/Skill/AgentDefinition/MCP 声明/权限清单，
由引擎统一挂载。
"""

from __future__ import annotations

from typing import Any

from nono_agent.agents.definition import AgentDefinition
from nono_agent.mcp.config import MCPServerConfig
from nono_agent.plugins.manifest import PluginManifest
from nono_agent.rules.models import Rule
from nono_agent.skills.loader import parse_skill_markdown
from nono_agent.skills.models import Skill


def activate_rules(manifest: PluginManifest) -> list[Rule]:
    """读取插件 rules/ 目录的 markdown 规则。"""
    rules: list[Rule] = []
    rules_dir = manifest.directory / manifest.rules_dir
    if not rules_dir.is_dir():
        return rules
    for file in sorted(rules_dir.glob("*.md")):
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = parse_skill_markdown(text)
        body = body.strip()
        if not body:
            continue
        globs = meta.get("globs", [])
        if isinstance(globs, str):
            globs = [globs]
        rules.append(
            Rule(
                name=f"{manifest.name}/{file.name}",
                content=body,
                source=str(file),
                scope="plugin",
                globs=[str(g) for g in globs],
                priority=20,  # 插件规则高于普通项目规则
            )
        )
    return rules


def activate_skills(manifest: PluginManifest) -> list[Skill]:
    """读取插件 skills/ 目录的技能。"""
    skills: list[Skill] = []
    skills_dir = manifest.directory / manifest.skills_dir
    if not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = parse_skill_markdown(text)
        body = body.strip()
        if not body:
            continue
        tools_raw = meta.get("tools", [])
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []
        skills.append(
            Skill(
                name=str(meta.get("name") or skill_dir.name),
                description=str(meta.get("description") or skill_dir.name),
                content=body,
                source=str(skill_file),
                scope="plugin",
                tools=tools,
            )
        )
    return skills


def activate_agents(manifest: PluginManifest) -> list[AgentDefinition]:
    """读取插件 agents/ 目录的子 Agent 定义。"""
    agents: list[AgentDefinition] = []
    agents_dir = manifest.directory / manifest.agents_dir
    if not agents_dir.is_dir():
        return agents
    for file in sorted(agents_dir.glob("*.md")):
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = parse_skill_markdown(text)
        body = body.strip()
        if not body:
            continue
        tools_raw = meta.get("tools", [])
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []
        agents.append(
            AgentDefinition(
                name=str(meta.get("name") or file.stem),
                description=str(meta.get("description") or file.stem),
                system_prompt=body,
                tools=tools,
                model=str(meta["model"]) if meta.get("model") else None,
                source=str(file),
            )
        )
    return agents


def activate_mcp(manifest: PluginManifest) -> dict[str, MCPServerConfig]:
    """读取插件的 MCP server 声明。"""
    servers: dict[str, MCPServerConfig] = {}
    for name, cfg in manifest.mcp.items():
        if isinstance(cfg, dict):
            servers[name] = MCPServerConfig.from_dict(name, cfg)
    return servers


def activate_permissions(manifest: PluginManifest) -> dict[str, Any]:
    """返回插件声明的权限需求清单。"""
    return dict(manifest.permissions)
