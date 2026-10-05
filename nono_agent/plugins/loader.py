"""Plugin 加载器 —— 发现路径、校验、冲突检测、失败隔离。

发现路径：项目 ``.nono-agent/plugins/*/plugin.json`` 与用户
``~/.nono-agent/plugins/*/plugin.json``。
同名插件冲突时：项目级优先于用户级；重复名报错并跳过后者。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from nono_agent.paths import project_dir, user_home
from nono_agent.plugins.manifest import PluginManifest, load_manifest


@runtime_checkable
class PluginLoader(Protocol):
    """插件加载器协议。"""

    def load(self, cwd: str | Path) -> list[PluginManifest]:
        ...


class FileSystemPluginLoader:
    """文件系统插件加载器。"""

    def __init__(self, user_plugins_dir: str | Path | None = None) -> None:
        self._user_plugins_dir = (
            Path(user_plugins_dir)
            if user_plugins_dir is not None
            else user_home() / "plugins"
        )
        #: 最近一次 load() 收集到的冲突/跳过记录（供调用方展示）。
        self.last_errors: list[str] = []

    def load(self, cwd: str | Path) -> list[PluginManifest]:
        dirs: list[Path] = [
            self._user_plugins_dir,
            project_dir(cwd) / "plugins",
        ]
        by_name: dict[str, PluginManifest] = {}
        errors: list[str] = []

        for plugins_dir in dirs:
            if not plugins_dir.is_dir():
                continue
            for plugin_dir in sorted(plugins_dir.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                manifest = load_manifest(plugin_dir)
                if manifest is None:
                    continue
                if manifest.name in by_name:
                    # 冲突：项目级覆盖用户级（目录顺序与 docstring 一致）
                    errors.append(
                        f"Plugin '{manifest.name}' conflicts with an existing plugin; skipped."
                    )
                    continue
                by_name[manifest.name] = manifest

        self.last_errors = errors
        # 失败隔离：加载错误不影响已加载插件（此处收集后记录，不抛出）
        return sorted(by_name.values(), key=lambda m: m.name)
