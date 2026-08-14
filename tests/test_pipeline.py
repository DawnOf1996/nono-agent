"""Tests for the preprocessing pipeline."""

import pytest

from nono_agent.core.types import (
    AssistantMessage,
    SystemMessage,
    UserMessage,
)
from nono_agent.pipeline.auto_compact import AutoCompactStage
from nono_agent.pipeline.base import PipelineContext, PreprocessPipeline
from nono_agent.pipeline.microcompact import MicroCompactStage
from nono_agent.pipeline.snip_compact import SnipCompactStage
from nono_agent.pipeline.tool_result_budget import ToolResultBudgetStage


def make_user_msg(text: str, tool_result: str | None = None) -> UserMessage:
    content: list = [{"type": "text", "text": text}]
    if tool_result:
        content.append({
            "type": "tool_result",
            "tool_use_id": "tr_1",
            "content": tool_result,
        })
    return UserMessage(message={"role": "user", "content": content})


def make_assistant_msg(text: str, thinking: str | None = None) -> AssistantMessage:
    content: list = [{"type": "text", "text": text}]
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "sig"})
    return AssistantMessage(
        message={"role": "assistant", "content": content, "stop_reason": "end_turn"}
    )


class TestPreprocessPipeline:
    def test_register_by_priority(self):
        pipeline = PreprocessPipeline()
        pipeline.register(ToolResultBudgetStage())
        pipeline.register(AutoCompactStage())
        pipeline.register(MicroCompactStage())

        # Should be sorted by priority
        names = [s.name for s in pipeline.stages]
        assert names == ["tool_result_budget", "microcompact", "auto_compact"]

    def test_register_before(self):
        pipeline = PreprocessPipeline()
        pipeline.register(AutoCompactStage())
        pipeline.register(ToolResultBudgetStage(), before="auto_compact")

        names = [s.name for s in pipeline.stages]
        assert names.index("tool_result_budget") < names.index("auto_compact")

    def test_register_after(self):
        pipeline = PreprocessPipeline()
        pipeline.register(ToolResultBudgetStage())
        pipeline.register(SnipCompactStage(), after="tool_result_budget")

        names = [s.name for s in pipeline.stages]
        assert names.index("tool_result_budget") < names.index("snip_compact")

    def test_unregister(self):
        pipeline = PreprocessPipeline()
        pipeline.register(ToolResultBudgetStage())
        pipeline.register(AutoCompactStage())

        removed = pipeline.unregister("tool_result_budget")
        assert removed is not None
        assert len(pipeline) == 1


class TestToolResultBudgetStage:
    @pytest.mark.asyncio
    async def test_truncates_large_tool_result(self):
        stage = ToolResultBudgetStage(budget_per_result=100)
        large_text = "x" * 10000

        messages = [make_user_msg("query", tool_result=large_text)]
        ctx = PipelineContext(model_id="claude-sonnet-4-20250514")

        result_messages, result_ctx = await stage.execute(messages, ctx)

        # Tool result should be truncated
        content = result_messages[0].message["content"]
        tool_result = content[1]
        assert len(tool_result["content"]) < len(large_text)


class TestSnipCompactStage:
    def test_should_not_run_small_context(self):
        stage = SnipCompactStage()
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            estimated_tokens=1000,
        )
        assert stage.should_run([], ctx) is False

    def test_should_run_large_context(self):
        stage = SnipCompactStage(threshold=0.1)  # Low threshold for testing
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            estimated_tokens=50000,
        )
        assert stage.should_run([], ctx) is True


class TestMicroCompactStage:
    @pytest.mark.asyncio
    async def test_strips_thinking_blocks(self):
        stage = MicroCompactStage(turn_lookback=1, max_text_tokens=1000)
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            estimated_tokens=150000,  # >50% of 200k window
        )

        messages = [
            make_assistant_msg("Old response", thinking="Very long thinking content " * 100),
            make_user_msg("next question"),
            make_assistant_msg("Latest response"),
        ]

        result, _ = await stage.execute(messages, ctx)

        # First assistant message should have thinking stripped
        old_content = result[0].message["content"]
        thinking_blocks = [
            b
            for b in old_content
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert len(thinking_blocks) == 0


class TestAutoCompactStage:
    def test_should_run_above_threshold(self):
        stage = AutoCompactStage()
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            estimated_tokens=170000,  # >80% of 200k
        )
        assert stage.should_run([], ctx) is True

    def test_should_not_run_below_threshold(self):
        stage = AutoCompactStage()
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            estimated_tokens=50000,
        )
        assert stage.should_run([], ctx) is False

    @pytest.mark.asyncio
    async def test_aggressive_compact_at_blocking_limit(self):
        stage = AutoCompactStage()
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            estimated_tokens=200000,  # >95% of 200k
        )

        messages = [
            make_user_msg("msg 1"),
            make_assistant_msg("resp 1"),
            make_user_msg("msg 2"),
            make_assistant_msg("resp 2"),
            make_user_msg("msg 3"),
            make_assistant_msg("resp 3"),
            make_user_msg("msg 4"),
            make_assistant_msg("resp 4"),
        ]

        result, _ = await stage.execute(messages, ctx)
        # Should have fewer messages + a compact boundary
        has_boundary = any(
            isinstance(m, SystemMessage) and m.subtype == "compact_boundary"
            for m in result
        )
        assert has_boundary
