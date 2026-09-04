"""Plan 模式工具 —— 进出计划模式（联动 `plan` 权限模式）。

进入 plan 模式后，权限策略切换为拒绝写副作用；退出恢复 default。
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.permissions.policy import PermissionMode
from nono_agent.tools.protocol import ToolBase


class EnterPlanModeTool(ToolBase):
    """进入计划模式（只读，禁止写副作用）。"""

    _name = "enter_plan_mode"
    _description = (
        "Enter plan mode: from the next step, write operations are denied until "
        "'exit_plan_mode' is called. Use this to explore and propose a plan "
        "without modifying anything."
    )
    _input_schema = {"type": "object", "properties": {}}
    _is_concurrent_safe = False
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        policy = getattr(context, "policy", None)
        if policy is None:
            return ToolResult(
                tool_use_id="",
                content="Error: no permission policy is attached to this session.",
                is_error=True,
                tool_name=self._name,
            )
        policy.request_mode(PermissionMode.PLAN)
        return ToolResult(
            tool_use_id="",
            content=(
                "Entered plan mode. Write operations will be denied "
                "from the next step."
            ),
            tool_name=self._name,
        )


class ExitPlanModeTool(ToolBase):
    """退出计划模式，恢复 default 权限。"""

    _name = "exit_plan_mode"
    _description = (
        "Exit plan mode. Default permissions are restored from the next step."
    )
    _input_schema = {"type": "object", "properties": {}}
    _is_concurrent_safe = False
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        policy = getattr(context, "policy", None)
        if policy is None:
            return ToolResult(
                tool_use_id="",
                content="Error: no permission policy is attached to this session.",
                is_error=True,
                tool_name=self._name,
            )
        policy.request_mode(PermissionMode.DEFAULT)
        return ToolResult(
            tool_use_id="",
            content=(
                "Exited plan mode. Default permissions will be restored "
                "from the next step."
            ),
            tool_name=self._name,
        )
