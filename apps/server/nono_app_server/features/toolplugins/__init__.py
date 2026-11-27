"""内置工具插件（产品定义）。

**这些插件属于产品，不属于引擎。** 引擎的 ``nono_agent/toolplugins`` 只提供
声明规范、执行器与运行时，不打包任何具体厂商集成；插件随本包一起发布，
启动时通过 :func:`register_builtin_plugins` 挂进引擎的发现链。

要加一个新插件：在这里新建 ``<名字>/plugin.json`` 照着写，
不需要改引擎，也不需要改前端（面板按清单里的 ``config`` 段渲染）。
"""

from __future__ import annotations

from pathlib import Path

from nono_agent.toolplugins import register_plugin_dir

#: 本包内置插件的根目录。每个子目录里放一份 plugin.json。
PLUGIN_DIR = Path(__file__).parent / "plugins"


def register_builtin_plugins() -> Path:
    """把本包的内置插件目录注册进引擎。幂等，可重复调用。"""
    return register_plugin_dir(PLUGIN_DIR)
