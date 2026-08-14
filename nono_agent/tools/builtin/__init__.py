"""生产级内置工具包。

替代旧的单文件 ``tools/builtin.py``（现仅保留薄 re-export）。
包含：Bash / Read / Write / Edit / Glob / Grep。
"""

from __future__ import annotations

from nono_agent.tools.builtin.ask_user import AskUserQuestionTool
from nono_agent.tools.builtin.bash import BashTool
from nono_agent.tools.builtin.demo import CalculatorTool, GetTimeTool
from nono_agent.tools.builtin.edit import EditFileTool
from nono_agent.tools.builtin.glob import GlobTool
from nono_agent.tools.builtin.grep import GrepTool
from nono_agent.tools.builtin.ls import LSTool
from nono_agent.tools.builtin.plan import EnterPlanModeTool, ExitPlanModeTool
from nono_agent.tools.builtin.read import ReadFileTool
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
    "CalculatorTool",
    "EditFileTool",
    "EnterPlanModeTool",
    "EnterWorktreeTool",
    "ExitPlanModeTool",
    "ExitWorktreeTool",
    "GetTimeTool",
    "GlobTool",
    "GrepTool",
    "ReadFileTool",
    "TodoWriteTool",
    "WriteFileTool",
    "create_builtin_tools",
]


def create_builtin_tools() -> list[ToolBase]:
    """创建所有内置工具实例（生产级默认集合）。"""
    tools: list[ToolBase] = [
        BashTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        GlobTool(),
        GrepTool(),
        LSTool(),
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
