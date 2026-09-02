"""Tests for the core engine and agentic loop."""

import asyncio

import pytest

from nono_agent.adapters.testing import MockTool, create_test_engine
from nono_agent.core.types import SDKResultMessage


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestQueryEngine:
    """Test QueryEngine lifecycle and message submission."""

    @pytest.mark.asyncio
    async def test_basic_message(self):
        """Test that a basic message returns a result."""
        engine = create_test_engine(
            responses=[
                {"text": "Hello! I'm a test assistant.", "tool_calls": []},
            ]
        )

        async with engine:
            messages = []
            async for msg in engine.submit_message("Hi"):
                messages.append(msg)

            assert len(messages) > 0
            result = messages[-1]
            assert isinstance(result, SDKResultMessage)
            assert result.subtype == "success"
            assert not result.is_error

    @pytest.mark.asyncio
    async def test_tool_call_flow(self):
        """Test that tool calls are executed and results returned."""
        tool = MockTool(
            name="calculator",
            response="42",
            concurrent_safe=True,
            write_side_effects=False,
        )

        engine = create_test_engine(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {"name": "calculator", "id": "tc_1", "input": {"query": "6*7"}}
                    ],
                },
                {"text": "The answer is 42.", "tool_calls": []},
            ],
            tools=[tool],
        )

        async with engine:
            messages = []
            async for msg in engine.submit_message("What is 6*7?"):
                messages.append(msg)

            # Verify tool was called
            assert tool.call_count == 1
            assert tool.last_input == {"query": "6*7"}

            # Verify final result
            result = messages[-1]
            assert isinstance(result, SDKResultMessage)

    @pytest.mark.asyncio
    async def test_abort(self):
        """Test that abort stops the loop."""
        engine = create_test_engine(
            responses=[
                {"text": "This should be interrupted.", "tool_calls": []},
            ]
        )

        async with engine:
            engine.abort("User cancelled")

            messages = []
            async for msg in engine.submit_message("Long task"):
                messages.append(msg)

            # Should still get a result (even if aborted)
            assert len(messages) > 0

    @pytest.mark.asyncio
    async def test_session_id(self):
        """Test that session ID is consistent."""
        engine = create_test_engine()

        async with engine:
            assert engine.session_id is not None
            assert len(engine.session_id) > 0

    @pytest.mark.asyncio
    async def test_token_tracking(self):
        """Test that token tracking accumulates."""
        engine = create_test_engine(
            responses=[
                {"text": "Response 1", "tool_calls": []},
            ]
        )

        async with engine:
            async for _ in engine.submit_message("Hello"):
                pass

            usage = engine.token_tracker.get_total_usage()
            assert usage.input_tokens >= 0
            assert engine.token_tracker.get_total_tokens() >= 0
            assert usage.output_tokens >= 0


class TestMockProvider:
    """Test the mock provider directly."""

    @pytest.mark.asyncio
    async def test_result_carries_context_usage(self):
        """结果消息与 token tracker 均携带上下文用量分析快照。"""
        engine = create_test_engine(
            responses=[{"text": "Hello!", "tool_calls": []}],
        )

        async with engine:
            messages = []
            async for msg in engine.submit_message("Hi"):
                messages.append(msg)

            result = messages[-1]
            assert isinstance(result, SDKResultMessage)
            ctx_usage = result.context_usage
            assert ctx_usage is not None
            # analyze_context_usage 口径：窗口 + 分项齐全
            assert ctx_usage["context_window"] == 200_000  # 测试引擎未配置 model_context
            assert ctx_usage["total_tokens"] > 0
            assert ctx_usage["message_count"] >= 1
            assert ctx_usage["system_prompt_tokens"] > 0
            # tracker 侧：latest + 历史同步
            assert engine.token_tracker.get_context_usage() == ctx_usage
            assert len(engine.token_tracker.get_context_usage_history()) == 1

    @pytest.mark.asyncio
    async def test_default_response(self):
        """Test that default response is returned when no scripts."""
        from nono_agent.adapters.testing import MockProvider
        provider = MockProvider()

        events = []
        async for event in provider.stream(
            messages=[],
            system_prompt="test",
            tools=[],
        ):
            events.append(event)

        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_scripted_responses(self):
        """Test that scripted responses are returned in order."""
        from nono_agent.adapters.testing import MockProvider
        provider = MockProvider(
            responses=[
                {"text": "First", "tool_calls": []},
                {"text": "Second", "tool_calls": []},
            ]
        )

        # First call
        events1 = []
        async for event in provider.stream(messages=[], system_prompt="", tools=[]):
            events1.append(event)

        # Second call
        events2 = []
        async for event in provider.stream(messages=[], system_prompt="", tools=[]):
            events2.append(event)

        assert len(events1) > 0
        assert len(events2) > 0
        assert provider._call_count == 2
