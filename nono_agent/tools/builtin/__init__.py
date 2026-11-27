"""生产级内置工具包。

替代旧的单文件 ``tools/builtin.py``（现仅保留薄 re-export）。
包含：Bash / Read / Write / Edit / Glob / Grep / WebFetch 等零配置原语。

**需要用户点头才开的能力不在这里**：搜索、图像生成这类要密钥、要开关的能力
走工具插件（``nono_agent.toolplugins``），默认关闭。内置浏览器控制也归到那一类
—— 它背后是一条无认证的本机调试端口，端口开着时本机任何进程都能驱动整个
应用，所以「用户同意」是硬前提，不是加分项。它仍然用 Python 写（浏览器 pane
是我们自己的），但**由插件来挂**：清单里 ``kind: "builtin"`` 声明，插件关就
完全不进工具表，见 :data:`PLUGIN_MANAGED_TOOL_FACTORIES`。

真正读页面/点元素的部分委托给 agent-browser 的 CLI（见
:mod:`nono_agent.tools.builtin.browser`）。
"""

from __future__ import annotations

from collections.abc import Callable

from nono_agent.core.config import EngineConfig, ShellConfig
from nono_agent.tools.builtin.ask_user import AskUserQuestionTool
from nono_agent.tools.builtin.bash import BashTool
from nono_agent.tools.builtin.browser import (
    BrowserClickTool,
    BrowserNavigateTool,
    BrowserOpenTool,
    BrowserPressTool,
    BrowserReadTool,
    BrowserScrollTool,
    BrowserSnapshotTool,
    BrowserTabsTool,
    BrowserTypeTool,
    BrowserUseTool,
)
from nono_agent.tools.builtin.demo import CalculatorTool, GetTimeTool
from nono_agent.tools.builtin.edit import EditFileTool
from nono_agent.tools.builtin.glob import GlobTool
from nono_agent.tools.builtin.grep import GrepTool
from nono_agent.tools.builtin.job_tools import JobKillTool, JobOutputTool
from nono_agent.tools.builtin.ls import LSTool
from nono_agent.tools.builtin.lsp import LSPTool
from nono_agent.tools.builtin.plan import EnterPlanModeTool, ExitPlanModeTool
from nono_agent.tools.builtin.read import ReadFileTool
from nono_agent.tools.builtin.read_image import ReadImageTool
from nono_agent.tools.builtin.task import TaskStore, create_task_tools
from nono_agent.tools.builtin.todo_write import TodoWriteTool
from nono_agent.tools.builtin.tool_search import ToolSearchTool
from nono_agent.tools.builtin.web_fetch import WebFetchTool
from nono_agent.tools.builtin.worktree import EnterWorktreeTool, ExitWorktreeTool
from nono_agent.tools.builtin.write import WriteFileTool
from nono_agent.tools.protocol import ToolBase

__all__ = [
    "AskUserQuestionTool",
    "BashTool",
    "BrowserClickTool",
    "BrowserNavigateTool",
    "BrowserOpenTool",
    "BrowserPressTool",
    "BrowserReadTool",
    "BrowserScrollTool",
    "BrowserSnapshotTool",
    "BrowserTabsTool",
    "BrowserTypeTool",
    "BrowserUseTool",
    "CalculatorTool",
    "EditFileTool",
    "EnterPlanModeTool",
    "EnterWorktreeTool",
    "ExitPlanModeTool",
    "ExitWorktreeTool",
    "GetTimeTool",
    "GlobTool",
    "GrepTool",
    "JobKillTool",
    "JobOutputTool",
    "PLUGIN_MANAGED_TOOL_FACTORIES",
    "ReadFileTool",
    "ReadImageTool",
    "TodoWriteTool",
    "WriteFileTool",
    "create_builtin_tools",
    "create_plugin_builtin_tool",
]


#: **由工具插件托管**的内置工具：清单里用 ``kind: "builtin"`` 声明它们，
#: 只有插件被启用时才进工具表。``create_builtin_tools()`` 因此不能无条件造一份 ——
#: 否则插件关了工具还在，开关就成了摆设。
#:
#: 这里存的是**名字 → 工厂**而不是点分导入路径：清单是数据，让数据去指定要
#: import 什么，等于把「能执行任意 Python」的口子开给任何能写 plugin.json 的人。
#: 名字写错只会拿到 None，报错，不会有别的后果。
PLUGIN_MANAGED_TOOL_FACTORIES: dict[str, Callable[[], ToolBase]] = {
    "browser_open": BrowserOpenTool,
    "browser_tabs": BrowserTabsTool,
    "browser_use": BrowserUseTool,
    "browser_snapshot": BrowserSnapshotTool,
    "browser_click": BrowserClickTool,
    "browser_type": BrowserTypeTool,
    "browser_press": BrowserPressTool,
    "browser_read": BrowserReadTool,
    "browser_scroll": BrowserScrollTool,
    "browser_navigate": BrowserNavigateTool,
}


def create_plugin_builtin_tool(name: str) -> ToolBase | None:
    """按名字造一个插件托管的内置工具；不在册返回 ``None``。"""
    factory = PLUGIN_MANAGED_TOOL_FACTORIES.get(name)
    return factory() if factory is not None else None


def create_builtin_tools(config: EngineConfig | None = None) -> list[ToolBase]:
    """创建所有内置工具实例（生产级默认集合）。

    ``config`` 可选：不传时各工具用各自的默认配置。传了则取其中的 shell
    参数下发给 :class:`BashTool`。

    **不含** :data:`PLUGIN_MANAGED_TOOL_FACTORIES` 里的那些 —— 它们由插件运行时
    按开关装配。
    """
    shell_config = config.shell if config is not None else ShellConfig()
    tools: list[ToolBase] = [
        BashTool(config=shell_config),
        # 后台作业的读/停。注册表由引擎经 ToolUseContext 注入，三个工具同源。
        JobOutputTool(),
        JobKillTool(),
        ReadFileTool(),
        ReadImageTool(),
        WriteFileTool(),
        EditFileTool(),
        GlobTool(),
        GrepTool(),
        LSTool(),
        LSPTool(),
        WebFetchTool(),
        ToolSearchTool(),
        AskUserQuestionTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
    ]
    # Task 工具族共享同一个会话级 TaskStore
    tools.extend(create_task_tools(TaskStore()))
    return tools
