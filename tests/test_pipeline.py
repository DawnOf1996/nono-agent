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
            context_window=200_000,
            estimated_tokens=1000,
        )
        assert stage.should_run([], ctx) is False

    def test_should_run_large_context(self):
        stage = SnipCompactStage(threshold=0.1)  # Low threshold for testing
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            context_window=200_000,
            estimated_tokens=50000,
        )
        assert stage.should_run([], ctx) is True

    def test_small_configured_window_triggers_earlier(self):
        """窗口大小来自配置（model_context），不依赖模型 ID 前缀。"""
        stage = SnipCompactStage()  # 默认阈值 0.60
        # 同样的 token 量：配置小窗口 → 触发；配置大窗口 → 不触发
        small = PipelineContext(context_window=8_000, estimated_tokens=6_000)
        large = PipelineContext(context_window=200_000, estimated_tokens=6_000)
        assert stage.should_run([], small) is True
        assert stage.should_run([], large) is False


class TestMicroCompactStage:
    def test_should_run_respects_configured_window(self):
        stage = MicroCompactStage()
        assert stage.should_run(
            [], PipelineContext(context_window=10_000, estimated_tokens=6_000)
        ) is True
        assert stage.should_run(
            [], PipelineContext(context_window=200_000, estimated_tokens=6_000)
        ) is False

    @pytest.mark.asyncio
    async def test_strips_thinking_blocks(self):
        stage = MicroCompactStage(turn_lookback=1, max_text_tokens=1000)
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            context_window=200_000,
            estimated_tokens=150000,  # >50% of configured window
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
            context_window=200_000,
            estimated_tokens=170000,  # >80% of configured window
        )
        assert stage.should_run([], ctx) is True

    def test_should_not_run_below_threshold(self):
        stage = AutoCompactStage()
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            context_window=200_000,
            estimated_tokens=50000,
        )
        assert stage.should_run([], ctx) is False

    @pytest.mark.asyncio
    async def test_aggressive_compact_at_blocking_limit(self):
        stage = AutoCompactStage()
        ctx = PipelineContext(
            model_id="claude-sonnet-4-20250514",
            context_window=200_000,
            estimated_tokens=200000,  # >=95% of configured window
        )
        ctx.overhead_tokens = 1_234  # 系统提示词/工具开销应保留在重估算中

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

        result, result_ctx = await stage.execute(messages, ctx)
        # Should have fewer messages + a compact boundary
        has_boundary = any(
            isinstance(m, SystemMessage) and m.subtype == "compact_boundary"
            for m in result
        )
        assert has_boundary
        # 重估算 = 消息 token + 固定开销（system/tools）
        from nono_agent.utils.tokens import estimate_messages_tokens

        message_only = estimate_messages_tokens(
            [m.message for m in result if hasattr(m, "message")]
        )
        assert result_ctx.estimated_tokens == message_only + 1_234


class TestProviderFloorTokens:
    """provider 实测下界参与阈值判定 —— 防止估算偏低导致压缩触发过晚。

    背景：``estimated_tokens`` 是引擎估算，会因 tokenizer 差异、富内容块
    未建模等原因系统性偏低。provider 上报的 ``input + cache_read +
    cache_creation`` 是**实际**带进请求的上下文大小，一轮内单调增长，
    因此可作为下一次请求的合法下界。
    """

    def test_effective_is_estimate_when_no_floor(self):
        ctx = PipelineContext(estimated_tokens=1000)
        assert ctx.effective_tokens == 1000

    def test_effective_takes_max_with_floor(self):
        ctx = PipelineContext(estimated_tokens=1000, provider_floor_tokens=2500)
        assert ctx.effective_tokens == 2500

        # 估算高于下界时以估算为准
        ctx2 = PipelineContext(estimated_tokens=9000, provider_floor_tokens=2500)
        assert ctx2.effective_tokens == 9000

    def test_floor_not_zero_falsy_default(self):
        """默认 0 —— 没有实测样本时行为与改造前完全一致。"""
        assert PipelineContext().provider_floor_tokens == 0
        assert PipelineContext(estimated_tokens=42).effective_tokens == 42

    def test_reestimate_does_not_clobber_floor(self):
        """关键设计：stage 整体重算 estimated_tokens 时兜底值必须存活。

        snip_compact / auto_compact 会用 _reestimate() 把 estimated_tokens
        重算成新的纯估算值；兜底值必须独立，否则会被立刻冲掉。
        """
        ctx = PipelineContext(
            estimated_tokens=1_000_000, provider_floor_tokens=900_000
        )
        ctx.estimated_tokens = 500  # 模拟 stage 重算
        assert ctx.effective_tokens == 900_000

    def test_microcompact_triggers_earlier_with_floor(self):
        """估算低于 50% 但 provider 实测超 50% 时，MicroCompact 必须触发。"""
        stage = MicroCompactStage()
        window = 100_000
        msgs = [make_user_msg(f"m{i}") for i in range(40)]

        below = PipelineContext(
            context_window=window, estimated_tokens=int(window * 0.4)
        )
        assert stage.should_run(msgs, below) is False

        raised = PipelineContext(
            context_window=window,
            estimated_tokens=int(window * 0.4),
            provider_floor_tokens=int(window * 0.6),
        )
        assert stage.should_run(msgs, raised) is True

    def test_snip_compact_triggers_earlier_with_floor(self):
        from nono_agent.pipeline.snip_compact import SnipCompactStage as _Snip

        stage = _Snip(threshold=0.3)
        window = 100_000
        msgs = [make_user_msg(f"m{i}") for i in range(40)]

        assert stage.should_run(msgs, PipelineContext(context_window=window,
                                                      estimated_tokens=int(window * 0.1))) is False
        assert stage.should_run(
            msgs,
            PipelineContext(context_window=window,
                            estimated_tokens=int(window * 0.1),
                            provider_floor_tokens=int(window * 0.5)),
        ) is True

    @pytest.mark.asyncio
    async def test_auto_compact_blocking_uses_floor(self):
        """95% 阻塞线同样吃兜底值 —— 最后一道防线不能被低估绕过。"""
        window = 100_000
        msgs = [make_user_msg(f"m{i}") for i in range(20)]
        stage = AutoCompactStage()

        # 估算 70%（未到 80% 自动压缩线），但 provider 实测 97%
        ctx = PipelineContext(
            context_window=window,
            estimated_tokens=int(window * 0.70),
            provider_floor_tokens=int(window * 0.97),
        )
        assert stage.should_run(msgs, ctx) is True

        _, new_ctx = await stage.execute(msgs, ctx)
        # 走到了 blocking 分支 -> 创建了压缩边界
        assert new_ctx.metadata.get("compact_boundary_created") is True
        # 压缩后兜底值仍在字段里（由 loop/engine 在建边界时清零），
        # 而 estimated_tokens 已被 stage 重算
        assert new_ctx.estimated_tokens < int(window * 0.97)

    @pytest.mark.asyncio
    async def test_estimate_below_floor_does_not_trigger(self):
        """反向护栏：兜底值没超线时，行为必须与改造前一致（不误触发）。"""
        window = 100_000
        msgs = [make_user_msg(f"m{i}") for i in range(4)]
        stage = AutoCompactStage()
        ctx = PipelineContext(
            context_window=window,
            estimated_tokens=int(window * 0.10),
            provider_floor_tokens=int(window * 0.10),
        )
        assert stage.should_run(msgs, ctx) is False


class TestTokenTrackerProviderFloor:
    """TokenTracker 侧：记录 provider 实测输入量 + 压缩后清零。"""

    def _usage(self, **kw):
        from nono_agent.core.types import Usage

        return Usage(**kw)

    def test_starts_at_zero(self):
        from nono_agent.observability.token_tracker import TokenTracker

        assert TokenTracker().provider_context_floor() == 0

    def test_records_input_buckets_only(self):
        """只算输入三桶 —— output_tokens 不属于上下文占用。"""
        from nono_agent.observability.token_tracker import TokenTracker

        tt = TokenTracker()
        tt.record_usage(
            self._usage(
                input_tokens=100,
                cache_read_input_tokens=5_000,
                cache_creation_input_tokens=200,
                output_tokens=999,
            )
        )
        assert tt.provider_context_floor() == 100 + 5_000 + 200

    def test_tracks_latest_not_sum(self):
        """取最近一次，不是累加 —— 一轮内每步都整段重发，累加会爆表。"""
        from nono_agent.observability.token_tracker import TokenTracker

        tt = TokenTracker()
        tt.record_usage(self._usage(cache_read_input_tokens=1_000))
        tt.record_usage(self._usage(cache_read_input_tokens=2_000))
        assert tt.provider_context_floor() == 2_000

    def test_invalidate_clears_floor(self):
        from nono_agent.observability.token_tracker import TokenTracker

        tt = TokenTracker()
        tt.record_usage(self._usage(cache_read_input_tokens=8_000))
        assert tt.provider_context_floor() == 8_000
        tt.invalidate_provider_context()
        assert tt.provider_context_floor() == 0
