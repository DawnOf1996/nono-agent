"""TodoWrite 工具 —— 轻量任务列表。

对标 Claude Code TodoWrite：模型每次传入**完整**任务列表（替换式），
工具校验字段与状态枚举后回显摘要。任务状态：
``pending`` / ``in_progress`` / ``completed``。
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.protocol import ToolBase

_VALID_STATUSES = {"pending", "in_progress", "completed"}


class TodoWriteTool(ToolBase):
    """记录并更新任务列表。"""

    _name = "todo_write"
    _description = (
        "Track progress on a multi-step task with a lightweight todo list. "
        "Pass the COMPLETE list each time (it replaces the previous one). "
        "Each item needs a 'content' (short imperative) and a 'status' "
        "('pending', 'in_progress', or 'completed'). Keep at least one item "
        "'in_progress' while work remains."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Task description."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        todos = input_params.get("todos")
        if not isinstance(todos, list):
            return ToolResult(
                tool_use_id="",
                content="Error: 'todos' must be a list.",
                is_error=True,
                tool_name=self._name,
            )

        # 校验
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                return ToolResult(
                    tool_use_id="",
                    content=f"Error: todo #{i} is not an object.",
                    is_error=True,
                    tool_name=self._name,
                )
            if not isinstance(item.get("content"), str) or not item["content"].strip():
                return ToolResult(
                    tool_use_id="",
                    content=f"Error: todo #{i} is missing a non-empty 'content'.",
                    is_error=True,
                    tool_name=self._name,
                )
            if item.get("status") not in _VALID_STATUSES:
                return ToolResult(
                    tool_use_id="",
                    content=(
                        f"Error: todo #{i} has invalid status {item.get('status')!r}; "
                        f"must be one of {sorted(_VALID_STATUSES)}."
                    ),
                    is_error=True,
                    tool_name=self._name,
                )

        counts = {s: 0 for s in _VALID_STATUSES}
        for item in todos:
            counts[item["status"]] += 1

        summary = (
            f"Recorded {len(todos)} todo(s): "
            + ", ".join(f"{counts[s]} {s}" for s in sorted(_VALID_STATUSES))
            + "."
        )
        return ToolResult(
            tool_use_id="",
            content=summary,
            tool_name=self._name,
        )
