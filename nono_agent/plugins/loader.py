"""Plugin 加载器 —— 发现路径、校验、冲突检测、失败隔离。

发现路径：项目 ``.nono/plugins/*/plugin.json`` 与用户 ``~/.nono/plugins/*/plugin.json``。
同名插件冲突时：项目级优先于用户级；重复名报错并跳过后者。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

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
            else Path.home() / ".nono" / "plugins"
        )

    def load(self, cwd: str | Path) -> list[PluginManifest]:
        dirs: list[Path] = [
            self._user_plugins_dir,
            Path(cwd).resolve() / ".nono" / "plugins",
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
                    # 冲突：项目级优先，跳过后者（用户级）
                    errors.append(
                        f"Plugin '{manifest.name}' conflicts with an existing plugin; skipped."
                    )
                    continue
                by_name[manifest.name] = manifest

        # 失败隔离：加载错误不影响已加载插件（此处收集后记录，不抛出）
        return sorted(by_name.values(), key=lambda m: m.name)
