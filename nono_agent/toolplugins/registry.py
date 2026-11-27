"""内置插件目录注册表 —— 引擎与产品之间的唯一接缝。

引擎只认「一个装着 ``plugin.json`` 的目录」，不知道里面是什么插件。具体的
厂商插件（minimax-image、tavily……）是**产品定义**，归产品包所有，随产品一起
发布；引擎不打包它们。

产品侧在启动时调一次 :func:`register_plugin_dir` 把自己的插件目录挂上::

    from nono_agent.toolplugins import register_plugin_dir
    register_plugin_dir(Path(__file__).parent / "plugins")

注册顺序即优先级：先注册的在前，后注册的同名插件覆盖先注册的；用户级与项目级
仍然排在所有注册目录之后。
"""

from __future__ import annotations

from pathlib import Path

_REGISTERED: list[Path] = []


def register_plugin_dir(path: str | Path) -> Path:
    """注册一个内置插件目录。重复注册同一路径是空操作。"""
    resolved = Path(path).expanduser()
    if not resolved.is_dir():
        raise FileNotFoundError(f"plugin dir not found: {resolved}")
    key = str(resolved.resolve())
    if all(str(item.resolve()) != key for item in _REGISTERED):
        _REGISTERED.append(resolved)
    return resolved


def registered_plugin_dirs() -> list[Path]:
    """已注册的内置插件目录（注册顺序）。"""
    return list(_REGISTERED)


def clear_plugin_dirs() -> None:
    """清空注册表 —— 仅供测试在用例之间复位全局状态。"""
    _REGISTERED.clear()


def plugin_dirs_in(root: str | Path) -> list[Path]:
    """列出某个根目录下所有带 ``plugin.json`` 的子目录。"""
    base = Path(root)
    if not base.is_dir():
        return []
    return [item for item in sorted(base.iterdir()) if (item / "plugin.json").is_file()]
