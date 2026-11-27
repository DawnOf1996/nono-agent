"""Tests for context window config resolution + context usage analysis."""

import json

import pytest

from nono_agent.core.config import EngineConfig, ProviderConfig
from nono_agent.pipeline.base import PipelineContext
from nono_agent.pipeline.snip_compact import SnipCompactStage
from nono_agent.utils.tokens import (
    AUTO_COMPACT_THRESHOLD,
    BLOCKING_LIMIT_THRESHOLD,
    DEFAULT_CONTEXT_WINDOW,
    analyze_context_usage,
    count_tokens,
    estimate_message_tokens,
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


class TestBlockTypeCoverage:
    """每种内容块都必须计入估算 —— 漏算会让压缩阈值偏晚触发。

    背景：历史实现无脑读 ``block["text"]``，而 thinking 的正文在
    ``block["thinking"]``、redacted_thinking 在 ``block["data"]``，
    两个键都对不上，导致每一步的思考内容计 0；tool_result 的
    ``content`` 为富内容 list 时也整块漏算。
    """

    def test_thinking_block_is_counted(self):
        """thinking 正文在 block['thinking'] 而非 block['text']。"""
        thought = "推理内容 " * 300
        msg = {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": thought, "signature": "sig"}],
        }
        estimated = estimate_message_tokens(msg)
        truth = count_tokens(thought)
        # 至少要覆盖正文本身（允许少量结构性误差，不允许漏算主体）
        assert truth > 500, "测试数据要足够大才有区分度"
        assert estimated >= truth * 0.9, f"thinking 漏算: {estimated} < {truth}"

    def test_redacted_thinking_is_counted(self):
        """redacted_thinking 的密文在 block['data']。"""
        data = "ENC" * 1000
        msg = {"role": "assistant", "content": [{"type": "redacted_thinking", "data": data}]}
        assert estimate_message_tokens(msg) >= count_tokens(data) * 0.9

    def test_tool_result_list_content_is_counted(self):
        """富内容 list 形态的 tool_result 不能整块漏算。"""
        text = "文件内容 " * 500
        as_str = {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}],
        }
        as_list = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }
        truth = count_tokens(text)
        assert estimate_message_tokens(as_str) >= truth * 0.9
        assert estimate_message_tokens(as_list) >= truth * 0.9

    def test_tool_use_input_both_forms_counted(self):
        """流式期 input 是 JSON 字符串，API 期是 dict，两者都要计入。"""
        payload = {"path": "a" * 2000, "limit": 100}
        as_dict = {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "read", "input": payload}],
        }
        as_json = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "read",
                    "input": json.dumps(payload, ensure_ascii=False),
                }
            ],
        }
        truth = count_tokens(json.dumps(payload, ensure_ascii=False))
        assert estimate_message_tokens(as_dict) >= truth * 0.9
        assert estimate_message_tokens(as_json) >= truth * 0.9

    def test_text_block_unchanged(self):
        """回归护栏：text 块的估算口径不应被改动。"""
        msg = {"role": "assistant", "content": [{"type": "text", "text": "hello world"}]}
        assert estimate_message_tokens(msg) == 4 + count_tokens("assistant") + count_tokens(
            "hello world"
        )

    def test_unknown_block_is_conservatively_counted(self):
        """未知块类型走保守估计 —— 高估可接受，漏算不可接受。"""
        msg = {
            "role": "assistant",
            "content": [{"type": "some_future_block", "payload": "y" * 2000}],
        }
        assert estimate_message_tokens(msg) >= count_tokens("y" * 2000) * 0.9

    def test_mixed_blocks_all_counted(self):
        """一条典型 agent 消息：thinking + text + tool_use 全部计入。"""
        msg = {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "思考 " * 200},
                {"type": "text", "text": "正文 " * 100},
                {"type": "tool_use", "id": "t1", "name": "read", "input": {"p": "z" * 500}},
            ],
        }
        only_text = {
            "role": "assistant",
            "content": [{"type": "text", "text": "正文 " * 100}],
        }
        # 有 thinking/tool_use 的消息必须明显更大
        assert estimate_message_tokens(msg) > estimate_message_tokens(only_text) + 200

    def test_analyze_context_usage_includes_thinking(self):
        """端到端：analyze_context_usage 必须把 thinking 算进 total_tokens。"""
        thought = "深度推理 " * 400
        base = [{"role": "user", "content": "问题"}]
        with_thinking = base + [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": thought},
                    {"type": "text", "text": "答案"},
                ],
            }
        ]
        without = base + [{"role": "assistant", "content": [{"type": "text", "text": "答案"}]}]

        a = analyze_context_usage(with_thinking, model_context=1_000_000)
        b = analyze_context_usage(without, model_context=1_000_000)
        delta = a["total_tokens"] - b["total_tokens"]
        assert delta >= count_tokens(thought) * 0.9, f"analyze 漏算 thinking: {delta}"

    def test_never_underestimates_truth(self):
        """综合护栏：估算值不得低于独立计算的真值。"""

        def truth(msg):
            total = 4 + count_tokens(msg.get("role", ""))
            content = msg.get("content", "")
            if isinstance(content, str):
                return total + count_tokens(content)
            for b in content:
                if b["type"] == "thinking":
                    total += count_tokens(b["thinking"])
                elif b["type"] == "text":
                    total += count_tokens(b["text"])
                elif b["type"] == "tool_use":
                    total += count_tokens(json.dumps(b["input"], ensure_ascii=False))
                elif b["type"] == "tool_result":
                    c = b["content"]
                    total += (
                        count_tokens(c)
                        if isinstance(c, str)
                        else count_tokens(json.dumps(c, ensure_ascii=False))
                    )
            return total

        messages = [
            {"role": "user", "content": "起始问题"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "思考链 " * 300},
                    {"type": "text", "text": "我去查一下 " * 50},
                    {"type": "tool_use", "id": "t1", "name": "read", "input": {"p": "q" * 300}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "输出 " * 400}],
                    }
                ],
            },
        ]
        usage = analyze_context_usage(messages, model_context=1_000_000)
        assert usage["messages_tokens"] >= sum(truth(m) for m in messages) * 0.95
