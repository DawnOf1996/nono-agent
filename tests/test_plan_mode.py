"""Tests for Plan mode tools (enter/exit plan)."""

import pytest

from nono_agent.permissions import PermissionMode, PermissionPolicy
from nono_agent.tools.builtin import EnterPlanModeTool, ExitPlanModeTool
from nono_agent.tools.context import ToolUseContext


@pytest.fixture
def ctx():
    return ToolUseContext(cwd="/tmp", policy=PermissionPolicy())


class TestPlanModeTools:
    @pytest.mark.asyncio
    async def test_enter_plan_mode(self, ctx):
        tool = EnterPlanModeTool()
        result = await tool.execute({}, ctx)
        assert not result.is_error
        assert ctx.policy.mode is PermissionMode.DEFAULT
        assert ctx.policy.pending_mode is PermissionMode.PLAN
        assert ctx.policy.commit_pending() == (
            PermissionMode.DEFAULT,
            PermissionMode.PLAN,
        )
        assert ctx.policy.mode is PermissionMode.PLAN

    @pytest.mark.asyncio
    async def test_exit_plan_mode(self, ctx):
        ctx.policy.set_mode(PermissionMode.PLAN)
        tool = ExitPlanModeTool()
        result = await tool.execute({}, ctx)
        assert not result.is_error
        assert ctx.policy.mode is PermissionMode.PLAN
        assert ctx.policy.pending_mode is PermissionMode.DEFAULT
        assert ctx.policy.commit_pending() == (
            PermissionMode.PLAN,
            PermissionMode.DEFAULT,
        )
        assert ctx.policy.mode is PermissionMode.DEFAULT

    @pytest.mark.asyncio
    async def test_no_policy_returns_error(self):
        ctx = ToolUseContext(cwd="/tmp")
        tool = EnterPlanModeTool()
        result = await tool.execute({}, ctx)
        assert result.is_error
