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
from nono_agent.tools.builtin.plan import EnterPlanModeTool, ExitPlanModeTool
from nono_agent.tools.builtin.read import ReadFileTool
from nono_agent.tools.builtin.todo_write import TodoWriteTool
from nono_agent.tools.builtin.write import WriteFileTool
from nono_agent.tools.protocol import ToolBase

__all__ = [
    "AskUserQuestionTool",
    "BashTool",
    "CalculatorTool",
    "EditFileTool",
    "EnterPlanModeTool",
    "ExitPlanModeTool",
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
    return [
        BashTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        GlobTool(),
        GrepTool(),
        AskUserQuestionTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
    ]
