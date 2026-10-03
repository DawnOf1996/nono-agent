"""工具插件 —— 把「开开关 + 填 API Key」变成模型可用的工具。

清单里的工具声明是纯数据（见 :mod:`nono_agent.toolplugins.spec`），由这里的
两个内置执行器解释：:mod:`~nono_agent.toolplugins.http_json` 直接打 HTTP，
:mod:`~nono_agent.toolplugins.runtime` 负责发现、鉴权、启停与失败隔离。

本包**不含任何具体插件**：厂商集成是产品定义，由产品包在启动时经
:func:`register_plugin_dir` 注册进来。
"""

from nono_agent.toolplugins.http_json import HttpJsonTool
from nono_agent.toolplugins.policy import ToolPluginPolicy, resolve_tool_policy
from nono_agent.toolplugins.registry import (
    clear_plugin_dirs,
    register_plugin_dir,
    registered_plugin_dirs,
)
from nono_agent.toolplugins.runtime import (
    ToolPluginRuntime,
    ToolPluginStatus,
    builtin_plugin_dirs,
    discover_plugins,
    start_tool_plugin_runtime,
)
from nono_agent.toolplugins.spec import (
    AuthSpec,
    HttpToolSpec,
    McpToolSpec,
    ToolSpec,
    ToolSpecError,
    parse_tool_specs,
)

__all__ = [
    "AuthSpec",
    "HttpJsonTool",
    "HttpToolSpec",
    "McpToolSpec",
    "ToolPluginPolicy",
    "ToolPluginRuntime",
    "ToolPluginStatus",
    "ToolSpec",
    "ToolSpecError",
    "builtin_plugin_dirs",
    "clear_plugin_dirs",
    "discover_plugins",
    "parse_tool_specs",
    "register_plugin_dir",
    "registered_plugin_dirs",
    "resolve_tool_policy",
    "start_tool_plugin_runtime",
]
