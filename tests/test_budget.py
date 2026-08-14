"""Tests for loop budget gating (task_budget)."""

import pytest

from nono_agent.adapters.testing import MockProvider, MockTool
from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import SDKResultMessage
from nono_agent.providers.base import ProviderRegistry


def _make_engine(**config_kwargs) -> QueryEngine:
    provider = MockProvider(
        responses=[
            {"text": "first", "tool_calls": []},
            {"text": "second", "tool_calls": []},
        ]
    )
    registry = ProviderRegistry()
    registry.register_instance("test-model", provider)
    config = EngineConfig(model="test-model", **config_kwargs)
    return QueryEngine(config, provider_registry=registry)


class TestBudgetGating:
    @pytest.mark.asyncio
    async def test_task_budget_zero(self):
        """task_budget total=0 should terminate with TASK_BUDGET_EXHAUSTED."""
        engine = _make_engine(task_budget={"total": 0})
        async with engine:
            result = None
            async for msg in engine.submit_message("hi"):
                if isinstance(msg, SDKResultMessage):
                    result = msg
            assert result is not None
            assert result.is_error
            assert result.subtype == "error_task_budget_exhausted"

    @pytest.mark.asyncio
    async def test_no_budget_runs_normally(self):
        engine = _make_engine()
        async with engine:
            result = None
            async for msg in engine.submit_message("hi"):
                if isinstance(msg, SDKResultMessage):
                    result = msg
            assert result is not None
            assert not result.is_error
            assert result.subtype == "success"


class TestPermissionDenialsSurfaced:
    @pytest.mark.asyncio
    async def test_denial_in_result(self):
        """Permission denials recorded by the executor appear in SDKResult."""
        from nono_agent.permissions import (
            PermissionMode,
            PermissionPolicy,
            PermissionRule,
            RuleAction,
        )

        tool = MockTool(name="write_file", write_side_effects=True, concurrent_safe=False)
        provider = MockProvider(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {"name": "write_file", "id": "t1", "input": {"query": "x"}}
                    ],
                },
                {"text": "done", "tool_calls": []},
            ]
        )
        registry = ProviderRegistry()
        registry.register_instance("test-model", provider)

        from nono_agent.tools.registry import ToolRegistry

        tool_registry = ToolRegistry()
        tool_registry.register(tool)

        policy = PermissionPolicy(
            mode=PermissionMode.PLAN,  # plan 模式拒绝写
            rules=[PermissionRule(action=RuleAction.DENY, tool_name="write_file")],
        )
        config = EngineConfig(model="test-model")
        engine = QueryEngine(
            config,
            provider_registry=registry,
            tool_registry=tool_registry,
            permission_policy=policy,
        )

        async with engine:
            result = None
            async for msg in engine.submit_message("do it"):
                if isinstance(msg, SDKResultMessage):
                    result = msg
            assert result is not None
            assert any(d["tool_name"] == "write_file" for d in result.permission_denials)
