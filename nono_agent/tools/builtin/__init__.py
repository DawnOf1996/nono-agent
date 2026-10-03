"""生产级内置工具包。

替代旧的单文件 ``tools/builtin.py``（现仅保留薄 re-export）。
包含：Bash / Read / Write / Edit / Glob / Grep。
"""

from __future__ import annotations

from nono_agent.core.config import EngineConfig, ShellConfig
from nono_agent.tools.builtin.ask_user import AskUserQuestionTool
from nono_agent.tools.builtin.bash import BashTool
from nono_agent.tools.builtin.demo import CalculatorTool, GetTimeTool
from nono_agent.tools.builtin.edit import EditFileTool
from nono_agent.tools.builtin.glob import GlobTool
from nono_agent.tools.builtin.grep import GrepTool
from nono_agent.tools.builtin.job_tools import JobKillTool, JobOutputTool
from nono_agent.tools.builtin.ls import LSTool
from nono_agent.tools.builtin.lsp import LSPTool
from nono_agent.tools.builtin.plan import EnterPlanModeTool, ExitPlanModeTool
from nono_agent.tools.builtin.read import ReadFileTool
from nono_agent.tools.builtin.task import TaskStore, create_task_tools
from nono_agent.tools.builtin.todo_write import TodoWriteTool
from nono_agent.tools.builtin.tool_search import ToolSearchTool
from nono_agent.tools.builtin.web_fetch import WebFetchTool
from nono_agent.tools.builtin.web_search import WebSearchTool
from nono_agent.tools.builtin.worktree import EnterWorktreeTool, ExitWorktreeTool
from nono_agent.tools.builtin.write import WriteFileTool
from nono_agent.tools.protocol import ToolBase

__all__ = [
    "AskUserQuestionTool",
    "BashTool",
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
    "ReadFileTool",
    "TodoWriteTool",
    "WriteFileTool",
    "create_builtin_tools",
]


def create_builtin_tools(config: EngineConfig | None = None) -> list[ToolBase]:
    """创建所有内置工具实例（生产级默认集合）。

    ``config`` 可选：不传时各工具用各自的默认配置。传了则取其中的 shell
    参数下发给 :class:`BashTool`。
    """
    shell_config = config.shell if config is not None else ShellConfig()
    tools: list[ToolBase] = [
        BashTool(config=shell_config),
        # 后台作业的读/停。注册表由引擎经 ToolUseContext 注入，三个工具同源。
        JobOutputTool(),
        JobKillTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        GlobTool(),
        GrepTool(),
        LSTool(),
        LSPTool(),
        WebFetchTool(),
        WebSearchTool(),
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
