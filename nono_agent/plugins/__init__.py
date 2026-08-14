"""Plugins —— 扩展分发单元（打包 Rules/Skills/Agents/Hooks/MCP/权限声明）。"""

from nono_agent.plugins.activate import (
    activate_agents,
    activate_mcp,
    activate_permissions,
    activate_rules,
    activate_skills,
)
from nono_agent.plugins.loader import FileSystemPluginLoader, PluginLoader
from nono_agent.plugins.manifest import PluginManifest, load_manifest

__all__ = [
    "FileSystemPluginLoader",
    "PluginLoader",
    "PluginManifest",
    "activate_agents",
    "activate_mcp",
    "activate_permissions",
    "activate_rules",
    "activate_skills",
    "load_manifest",
]
