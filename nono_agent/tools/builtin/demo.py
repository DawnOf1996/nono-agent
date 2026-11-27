"""演示工具（保留自旧 builtin.py，供 E2E 冒烟测试）。

生产场景应使用同包内的 Bash / Read / Write / Edit / Glob / Grep。
"""

from __future__ import annotations

import datetime
import math
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
            tool_name=self._name,
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

    _SAFE_NAMES = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "int": int,
        "float": float,
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
                tool_name=self._name,
            )

        try:
            result = eval(expr, {"__builtins__": {}}, self._SAFE_NAMES)  # noqa: S307
            return ToolResult(
                tool_use_id="",
                content=f"{expr} = {result}",
                tool_name=self._name,
            )
        except Exception as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error evaluating '{expr}': {exc}",
                is_error=True,
                tool_name=self._name,
            )
