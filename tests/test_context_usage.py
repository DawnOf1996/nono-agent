"""Tests for context window config resolution + context usage analysis."""

import pytest

from nono_agent.core.config import EngineConfig, ProviderConfig
from nono_agent.pipeline.base import PipelineContext
from nono_agent.pipeline.snip_compact import SnipCompactStage
from nono_agent.utils.tokens import (
    AUTO_COMPACT_THRESHOLD,
    BLOCKING_LIMIT_THRESHOLD,
    DEFAULT_CONTEXT_WINDOW,
    analyze_context_usage,
    estimate_messages_tokens,
    estimate_system_prompt_tokens,
    estimate_tools_tokens,
    resolve_context_window,
)


def _user_msg(text: str) -> dict:
    return {"role": "user", "content": text}


def _pipeline_msg(text: str):
    from tests.test_pipeline import make_user_msg

    return make_user_msg(text)


class TestResolveContextWindow:
    def test_configured_value_wins(self):
        assert resolve_context_window(32_000) == 32_000

    def test_fallback_when_unconfigured(self):
        assert resolve_context_window(None) == DEFAULT_CONTEXT_WINDOW
        assert resolve_context_window(0) == DEFAULT_CONTEXT_WINDOW
        assert resolve_context_window(-1) == DEFAULT_CONTEXT_WINDOW


class TestEngineConfigModelContext:
    def test_defaults_to_fallback(self):
        config = EngineConfig()
        assert config.effective_model_context == DEFAULT_CONTEXT_WINDOW

    def test_engine_level_model_context(self):
        config = EngineConfig(model_context=32_000)
        assert config.effective_model_context == 32_000

    def test_provider_model_context_takes_precedence(self):
        config = EngineConfig(
            model_context=32_000,
            provider=ProviderConfig(model_id="MiniMax-M3", model_context=8_000),
        )
        assert config.effective_model_context == 8_000

    def test_provider_without_context_falls_back_to_engine(self):
        config = EngineConfig(
            model_context=32_000,
            provider=ProviderConfig(model_id="MiniMax-M3"),
        )
        assert config.effective_model_context == 32_000

    def test_no_prefix_matching(self):
        """窗口大小不再随模型 ID 变化 —— 未配置时一律回退默认值。"""
        for model in ("gpt-4", "gpt-4o", "claude-3-5-sonnet-20241022", "o1-mini"):
            config = EngineConfig(model=model)
            assert config.effective_model_context == DEFAULT_CONTEXT_WINDOW


class TestEstimateParts:
    def test_empty_system_prompt(self):
        assert estimate_system_prompt_tokens("") == 0

    def test_system_prompt_positive(self):
        assert estimate_system_prompt_tokens("You are a helpful assistant.") > 0

    def test_empty_tools(self):
        assert estimate_tools_tokens(None) == 0
        assert estimate_tools_tokens([]) == 0

    def test_tools_positive(self):
        tools = [
            {
                "name": "bash",
                "description": "Run a bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
            {"name": "read", "description": "Read a file", "input_schema": {}},
        ]
        assert estimate_tools_tokens(tools) > 0


class TestAnalyzeContextUsage:
    def test_breakdown_sums_to_total(self):
        messages = [_user_msg("hello world"), {"role": "assistant", "content": "hi"}]
        system = "You are a helpful assistant."
        tools = [{"name": "bash", "description": "Run commands", "input_schema": {}}]

        usage = analyze_context_usage(messages, system_prompt=system, tools=tools)

        assert usage["system_prompt_tokens"] == estimate_system_prompt_tokens(system)
        assert usage["tools_tokens"] == estimate_tools_tokens(tools)
        assert usage["messages_tokens"] == estimate_messages_tokens(messages)
        assert usage["total_tokens"] == (
            usage["system_prompt_tokens"]
            + usage["tools_tokens"]
            + usage["messages_tokens"]
        )
        assert usage["tools_count"] == 1
        assert usage["message_count"] == 2

    def test_ratios_and_remaining(self):
        usage = analyze_context_usage(
            [_user_msg("hello")], model_context=1_000_000
        )
        window = usage["context_window"]
        assert window == 1_000_000
        assert usage["usage_ratio"] == round(usage["total_tokens"] / window, 4)
        assert usage["remaining_tokens"] == window - usage["total_tokens"]
        assert 0 <= usage["usage_ratio"] < 0.01  # 1M 窗口下几乎不占用

    def test_threshold_flags_match_pipeline(self):
        # 构造一个让消息本身越过 80%/95% 阈值的小窗口
        big_text = "x" * 40_000  # 兜底估算下 ≈ 10k tokens（tiktoken 下更大）
        usage = analyze_context_usage([_user_msg(big_text)], model_context=10_000)
        total = usage["total_tokens"]
        assert usage["is_auto_compact_threshold"] == (
            total >= int(10_000 * AUTO_COMPACT_THRESHOLD)
        )
        assert usage["is_at_blocking_limit"] == (
            total >= int(10_000 * BLOCKING_LIMIT_THRESHOLD)
        )


class TestOverheadAccounting:
    def test_pipeline_context_defaults(self):
        ctx = PipelineContext()
        assert ctx.context_window == DEFAULT_CONTEXT_WINDOW
        assert ctx.overhead_tokens == 0
        assert ctx.estimated_tokens == 0

    @pytest.mark.asyncio
    async def test_snip_execute_keeps_overhead_in_estimate(self):
        """SnipCompact 丢弃旧消息后，estimated_tokens 应包含固定开销。"""
        from tests.test_pipeline import make_assistant_msg, make_user_msg

        stage = SnipCompactStage()
        messages = [
            make_user_msg(f"question {i}") if i % 2 == 0 else make_assistant_msg(f"answer {i}")
            for i in range(12)
        ]
        message_tokens = estimate_messages_tokens(
            [m.message for m in messages if hasattr(m, "message")]
        )
        ctx = PipelineContext(
            context_window=40,  # 极小窗口强制丢弃（target = 40*0.6*0.8 ≈ 19）
            overhead_tokens=777,
            estimated_tokens=777 + message_tokens,  # 与 loop Phase1 的构造方式一致
        )

        result_messages, result_ctx = await stage.execute(messages, ctx)

        assert len(result_messages) < len(messages)  # 确实发生了丢弃
        assert result_ctx.overhead_tokens == 777     # 开销不被清掉
        remaining_message_tokens = estimate_messages_tokens(
            [m.message for m in result_messages if hasattr(m, "message")]
        )
        assert result_ctx.estimated_tokens == remaining_message_tokens + 777

    @pytest.mark.asyncio
    async def test_snip_not_triggered_below_threshold(self):
        stage = SnipCompactStage()
        ctx = PipelineContext(
            context_window=200_000,
            overhead_tokens=500,
            estimated_tokens=500 + 1_000,
        )
        messages = [_pipeline_msg("short")]

        result_messages, result_ctx = await stage.execute(messages, ctx)

        assert result_messages[0] is messages[0]  # 未丢弃任何消息
        assert result_ctx.estimated_tokens == 500 + 1_000  # 估算未被改写
