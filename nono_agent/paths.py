"""约定目录名 —— 用户运行时与项目配置共用 ``.nono-agent``。

用户侧：``~/.nono-agent/{config.yaml,credentials,sessions/,memory/,settings.json,mcp.json,rules/,...}``
项目侧：``<cwd>/.nono-agent/{config.yaml,settings.json,rules/,skills/,agents/,plugins/,hooks.json}``
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


def resolve_session_cwd(cwd: str | Path | None) -> str:
    """把工作目录收成绝对路径。

    会话 ``.header.json`` 与引擎沙箱都不能依赖 ``.`` 这类相对路径：
    resume 时进程 cwd 可能已经变了。
    """
    raw = str(cwd).strip() if cwd is not None else ""
    path = Path(raw).expanduser() if raw else Path.cwd()
    return str(path.resolve())
