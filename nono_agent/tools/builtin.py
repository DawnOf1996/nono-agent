"""内置演示工具 —— 用于 CLI 工具调用端到端测试。

包含三个简单工具：
- get_time: 返回当前日期时间
- calculator: 计算数学表达式
- read_file: 读取文件内容
"""

from __future__ import annotations

import datetime
import math
import os
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.protocol import ToolBase


class GetTimeTool(ToolBase):
    """返回当前日期和时间。"""

    _name = "get_time"
    _description = (
        "Get the current date and time. "
        "Use this when you need to know what time or date it is."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "Timezone offset like '+8' or '-5'. Default is local.",
            },
        },
        "required": [],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        tz_str = input_params.get("timezone", "")
        try:
            if tz_str:
                offset_hours = int(tz_str)
                tz = datetime.timezone(datetime.timedelta(hours=offset_hours))
                now = datetime.datetime.now(tz)
            else:
                now = datetime.datetime.now()
        except (ValueError, OverflowError):
            now = datetime.datetime.now()

        return ToolResult(
            tool_use_id="",
            content=now.strftime("%Y-%m-%d %H:%M:%S (%A)"),
            tool_name="get_time",
        )


class CalculatorTool(ToolBase):
    """计算数学表达式。"""

    _name = "calculator"
    _description = (
        "Evaluate a mathematical expression and return the result. "
        "Supports basic arithmetic (+, -, *, /, **), math functions (sin, cos, sqrt, log, etc.). "
        "Example: '2 ** 10 + sqrt(144)'"
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The mathematical expression to evaluate.",
            },
        },
        "required": ["expression"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    # 安全白名单 —— 仅允许这些数学函数
    _SAFE_NAMES = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "int": int,
        "float": float,
        # math 模块函数
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log10": math.log10,
        "log2": math.log2,
        "exp": math.exp,
        "ceil": math.ceil,
        "floor": math.floor,
        "pi": math.pi,
        "e": math.e,
    }

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        expr = input_params.get("expression", "")
        if not expr:
            return ToolResult(
                tool_use_id="",
                content="Error: No expression provided.",
                is_error=True,
                tool_name="calculator",
            )

        try:
            # 安全求值 —— 仅允许白名单中的名称
            result = eval(expr, {"__builtins__": {}}, self._SAFE_NAMES)  # noqa: S307
            return ToolResult(
                tool_use_id="",
                content=f"{expr} = {result}",
                tool_name="calculator",
            )
        except Exception as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error evaluating '{expr}': {exc}",
                is_error=True,
                tool_name="calculator",
            )


class ReadFileTool(ToolBase):
    """读取文件内容。"""

    _name = "read_file"
    _description = (
        "Read the contents of a file at the given path. "
        "Returns the file content as text. "
        "Only reads text files; binary files may produce garbled output."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The file path to read (relative to working directory or absolute).",
            },
        },
        "required": ["path"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    # 最大读取字节数（防止内存爆炸）
    _MAX_BYTES = 50_000

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        file_path = input_params.get("path", "")
        if not file_path:
            return ToolResult(
                tool_use_id="",
                content="Error: No file path provided.",
                is_error=True,
                tool_name="read_file",
            )

        # 相对路径基于 cwd 解析
        if not os.path.isabs(file_path):
            cwd = getattr(context, "cwd", ".")
            file_path = os.path.join(cwd, file_path)

        if not os.path.exists(file_path):
            return ToolResult(
                tool_use_id="",
                content=f"Error: File not found: {file_path}",
                is_error=True,
                tool_name="read_file",
            )

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(self._MAX_BYTES)
            if len(content) >= self._MAX_BYTES:
                content += f"\n... [truncated at {self._MAX_BYTES} bytes]"
            return ToolResult(
                tool_use_id="",
                content=content,
                tool_name="read_file",
            )
        except Exception as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error reading '{file_path}': {exc}",
                is_error=True,
                tool_name="read_file",
            )


class WriteFileTool(ToolBase):
    """写入内容到文件（测试权限授权流程）。"""

    _name = "write_file"
    _description = (
        "Write content to a file at the given path. "
        "Creates the file if it doesn't exist, or overwrites it if it does. "
        "This tool has write side effects and requires user approval."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The file path to write (relative to working directory or absolute).",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["path", "content"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        file_path = input_params.get("path", "")
        content = input_params.get("content", "")

        if not file_path:
            return ToolResult(
                tool_use_id="",
                content="Error: No file path provided.",
                is_error=True,
                tool_name="write_file",
            )

        # 相对路径基于 cwd 解析
        if not os.path.isabs(file_path):
            cwd = getattr(context, "cwd", ".")
            file_path = os.path.join(cwd, file_path)

        try:
            # 确保父目录存在
            parent_dir = os.path.dirname(file_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                tool_use_id="",
                content=f"Successfully wrote {len(content)} bytes to {file_path}",
                tool_name="write_file",
            )
        except Exception as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error writing to '{file_path}': {exc}",
                is_error=True,
                tool_name="write_file",
            )


def create_builtin_tools() -> list[ToolBase]:
    """创建所有内置工具实例。"""
    return [GetTimeTool(), CalculatorTool(), ReadFileTool(), WriteFileTool()]
