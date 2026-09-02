"""约定目录名 —— 用户运行时与项目配置共用 ``.nono-agent``。

用户侧：``~/.nono-agent/{config.json,sessions/,memory/,settings.json,rules/,...}``
项目侧：``<cwd>/.nono-agent/{settings.json,rules/,skills/,agents/,plugins/,hooks.json,mcp.json}``
"""

from __future__ import annotations

from pathlib import Path

CONFIG_DIR_NAME = ".nono-agent"


def user_home() -> Path:
    """用户级 ``~/.nono-agent`` 根目录。"""
    return Path.home() / CONFIG_DIR_NAME


def project_dir(root: str | Path) -> Path:
    """项目级 ``<root>/.nono-agent`` 根目录。"""
    return Path(root) / CONFIG_DIR_NAME
