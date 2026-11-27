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
    """退出计划模式，恢复进入 plan 之前的权限模式。

    恢复的是"进来之前是什么模式"，而不是拍脑袋回 default —— 从 bypass
    进 plan 再退出，应该还在 bypass，否则用户以为还在放行模式，
    实际上被降级了。
    """

    _name = "exit_plan_mode"
    _description = (
        "Exit plan mode. The permission mode in effect before plan mode is "
        "restored from the next step."
    )
    _input_schema = {"type": "object", "properties": {}}
    _is_concurrent_safe = False
    _has_write_side_effects = False
    # 退出计划模式必须有人点头 —— 否则模型可以自己把用户设下的只读限制解开，
    # 那是它单方面给自己开权限。不可被任何模式或白名单跳过。
    _always_requires_approval = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        policy = getattr(context, "policy", None)
        if policy is None:
            return ToolResult(
                tool_use_id="",
                content="Error: no permission policy is attached to this session.",
                is_error=True,
                tool_name=self._name,
            )
        target = getattr(policy, "pre_plan_mode", PermissionMode.DEFAULT)
        policy.request_mode(target)
        return ToolResult(
            tool_use_id="",
            content=(
                f"Exited plan mode. Permission mode will return to "
                f"'{target.value}' from the next step."
            ),
            tool_name=self._name,
        )
