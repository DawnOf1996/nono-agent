"""Tests for the core engine and agentic loop."""

import asyncio

import pytest

from nono_agent.core.types import SDKResultMessage, ToolResult
from tests.helpers import MockTool, create_test_engine


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
    async def test_tool_result_progress_carries_result(self):
        """工具跑完的进度帧要带上结果 —— 客户端据此把输出贴回工具行。"""
        tool = MockTool(name="calculator", response="42")
        engine = create_test_engine(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {"name": "calculator", "id": "tc_1", "input": {"query": "6*7"}}
                    ],
                },
                {"text": "done", "tool_calls": []},
            ],
            tools=[tool],
        )

        async with engine:
            messages = [msg async for msg in engine.submit_message("What is 6*7?")]

        notices = [
            msg
            for msg in messages
            if msg.type == "system" and msg.subtype == "tool_result"
        ]
        assert len(notices) == 1
        assert notices[0].tool_use_id == "tc_1"
        assert notices[0].tool_result == "42"
        assert notices[0].tool_is_error is False

    @pytest.mark.asyncio
    async def test_tool_result_progress_marks_error(self):
        """失败的工具在进度帧里标出 is_error。"""

        class FailingTool(MockTool):
            async def execute(self, input_params, context):
                self.call_count += 1
                return ToolResult(
                    tool_use_id="", content="boom", is_error=True, tool_name=self.name
                )

        tool = FailingTool(name="flaky", response="unused")
        engine = create_test_engine(
            responses=[
                {"text": "", "tool_calls": [{"name": "flaky", "id": "tc_2", "input": {}}]},
                {"text": "done", "tool_calls": []},
            ],
            tools=[tool],
        )

        async with engine:
            messages = [msg async for msg in engine.submit_message("go")]

        notices = [
            msg
            for msg in messages
            if msg.type == "system" and msg.subtype == "tool_result"
        ]
        assert len(notices) == 1
        assert notices[0].tool_result == "boom"
        assert notices[0].tool_is_error is True

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
        from tests.helpers import MockProvider
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
        from tests.helpers import MockProvider
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
