"""会话统计 —— 从事件流推导轮/步/用时/TTFT/TPS/token/上下文。"""

from nono_agent.session.events import (
    EVENT_ASSISTANT_CHUNK,
    EVENT_ASSISTANT_MESSAGE,
    EVENT_STEP_END,
    EVENT_STEP_START,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USAGE,
    SessionEvent,
)
from nono_agent.session.stats import build_session_stats


def _events(*specs, base_ts: int = 0):
    """按给定时间戳造事件流；``specs`` 每项是 (type, 相对毫秒, data)。

    这里直接构造 SessionEvent 而不用 ``SessionEvent.make`` —— 后者把额外
    关键字全塞进 ``data``，``timestamp`` 会被当成事件载荷而不是信封字段。
    """
    return [
        SessionEvent(
            seq=i + 1,
            type=type_,
            data=data,
            timestamp=float(base_ts + offset),
        )
        for i, (type_, offset, data) in enumerate(specs)
    ]


class TestSessionStatsBasics:
    def test_empty_stream_is_all_zero(self):
        stats = build_session_stats([])
        assert stats["turns"] == 0
        assert stats["steps"] == 0
        assert stats["tps"] is None
        assert stats["ttft_ms"] is None
        assert stats["context"] is None

    def test_counts_turns_and_steps(self):
        events = _events(
            (EVENT_TURN_START, 0, {}),
            (EVENT_STEP_START, 10, {"step": 1}),
            (EVENT_ASSISTANT_MESSAGE, 20, {"content": []}),
            (EVENT_STEP_END, 30, {"step": 1, "reason": "completed"}),
            (EVENT_TURN_END, 40, {"turn": 1, "reason": "completed"}),
            (EVENT_TURN_START, 50, {}),
            (EVENT_STEP_START, 60, {"step": 2}),
            (EVENT_ASSISTANT_MESSAGE, 70, {"content": []}),
            (EVENT_STEP_END, 80, {"step": 2, "reason": "completed"}),
            (EVENT_TURN_END, 90, {"turn": 2, "reason": "completed"}),
        )
        stats = build_session_stats(events)
        assert stats["turns"] == 2
        assert stats["steps"] == 2

    def test_sums_all_four_token_buckets(self):
        events = _events(
            (EVENT_USAGE, 0, {"usage": {"input_tokens": 10, "output_tokens": 5}}),
            (
                EVENT_USAGE,
                10,
                {
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 7,
                    }
                },
            ),
        )
        usage = build_session_stats(events)["usage"]
        assert usage["input_tokens"] == 13
        assert usage["output_tokens"] == 7
        assert usage["cache_read_input_tokens"] == 100
        assert usage["cache_creation_input_tokens"] == 7
        assert usage["total_tokens"] == 127

    def test_cache_hit_ratio_excludes_cache_creation(self):
        # 100 读回 / (100 读回 + 10 新鲜) = 90.9%；缓存写入不参与分母。
        events = _events(
            (
                EVENT_USAGE,
                0,
                {
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 999,
                    }
                },
            ),
        )
        assert build_session_stats(events)["usage"]["cache_hit_ratio"] == 0.9091

    def test_no_input_yields_no_cache_ratio(self):
        events = _events((EVENT_USAGE, 0, {"usage": {"output_tokens": 5}}))
        assert build_session_stats(events)["usage"]["cache_hit_ratio"] is None


class TestSessionStatsTiming:
    def test_splits_step_into_ttft_model_and_tool(self):
        events = _events(
            (EVENT_STEP_START, 0, {"step": 1}),
            (EVENT_ASSISTANT_MESSAGE, 1000, {"content": []}),  # 首 token 1s
            (EVENT_STEP_END, 3000, {"step": 1, "reason": "completed"}),  # 共 3s
        )
        stats = build_session_stats(events)
        assert stats["ttft_ms"] == 1000
        assert stats["model_duration_ms"] == 1000
        assert stats["tool_duration_ms"] == 2000
        # 模型用时 + 工具用时 恰好等于 step 总时长，不重不漏。
        assert stats["model_duration_ms"] + stats["tool_duration_ms"] == 3000

    def test_ttft_is_averaged_over_steps(self):
        events = _events(
            (EVENT_STEP_START, 0, {}),
            (EVENT_ASSISTANT_CHUNK, 1000, {"chunk": {}}),
            (EVENT_STEP_END, 2000, {}),
            (EVENT_STEP_START, 3000, {}),
            (EVENT_ASSISTANT_CHUNK, 5000, {"chunk": {}}),
            (EVENT_STEP_END, 6000, {}),
        )
        # (1000 + 2000) / 2
        assert build_session_stats(events)["ttft_ms"] == 1500

    def test_tps_measures_span_between_content_events(self):
        # 流式：首分片 1s 到达，末分片 2s 到达 → 生成 1s；20 个输出 token → 20 tok/s。
        events = _events(
            (EVENT_STEP_START, 0, {}),
            (EVENT_ASSISTANT_CHUNK, 1000, {"chunk": {}}),
            (EVENT_ASSISTANT_CHUNK, 2000, {"chunk": {}}),
            (EVENT_STEP_END, 5000, {}),
            (EVENT_USAGE, 5000, {"usage": {"input_tokens": 1, "output_tokens": 20}}),
        )
        assert build_session_stats(events)["tps"] == 20.0

    def test_tps_is_null_without_streamed_chunks(self):
        # 只有一条整段 assistant/message：首尾同一刻，生成耗时无从得知。
        # 此时不能把 step/end 之后的工具执行时间当成生成时间去凑一个假的 TPS。
        events = _events(
            (EVENT_STEP_START, 0, {}),
            (EVENT_ASSISTANT_MESSAGE, 1000, {}),
            (EVENT_STEP_END, 9000, {}),
            (EVENT_USAGE, 9000, {"usage": {"input_tokens": 1, "output_tokens": 20}}),
        )
        stats = build_session_stats(events)
        assert stats["tps"] is None
        # 用时本身仍然统计得到：模型 1s、工具 8s。
        assert stats["model_duration_ms"] == 1000
        assert stats["tool_duration_ms"] == 8000

    def test_step_without_content_events_is_ignored(self):
        # 请求一发出就失败：不能把这段空转算进模型用时。
        events = _events(
            (EVENT_STEP_START, 0, {}),
            (EVENT_STEP_END, 5000, {"reason": "api_error"}),
        )
        stats = build_session_stats(events)
        assert stats["model_duration_ms"] == 0
        assert stats["tool_duration_ms"] == 0
        assert stats["ttft_ms"] is None

    def test_unterminated_step_is_closed(self):
        # 流被硬断，最后一个 step 没有 step/end。
        events = _events(
            (EVENT_STEP_START, 0, {}),
            (EVENT_ASSISTANT_MESSAGE, 400, {}),
        )
        stats = build_session_stats(events)
        assert stats["model_duration_ms"] == 400


class TestSessionStatsContext:
    def test_uses_engine_context_snapshot(self):
        events = _events(
            (
                EVENT_USAGE,
                0,
                {
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                    "context": {
                        "context_window": 1_000_000,
                        "total_tokens": 143_000,
                        "usage_ratio": 0.143,
                        "system_prompt_tokens": 1500,
                        "tools_tokens": 5600,
                        "messages_tokens": 119_000,
                        "message_count": 12,
                    },
                },
            ),
        )
        context = build_session_stats(events)["context"]
        assert context["context_window"] == 1_000_000
        assert context["total_tokens"] == 143_000
        assert context["system_prompt_tokens"] == 1500
        assert context["tools_tokens"] == 5600
        assert context["messages_tokens"] == 119_000
        assert context["message_count"] == 12

    def test_falls_back_to_last_request_input_for_old_logs(self):
        # 旧版本日志没有 context 字段，用最后一次请求的输入三桶兜底。
        events = _events(
            (
                EVENT_USAGE,
                0,
                {
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 10,
                        "cache_read_input_tokens": 9000,
                    }
                },
            ),
        )
        context = build_session_stats(events)["context"]
        assert context["total_tokens"] == 10_000
        assert context["system_prompt_tokens"] is None
        assert context["context_window"] == 200_000

    def test_keeps_the_last_snapshot(self):
        events = _events(
            (EVENT_USAGE, 0, {"usage": {"input_tokens": 1}, "context": {"total_tokens": 10}}),
            (EVENT_USAGE, 10, {"usage": {"input_tokens": 1}, "context": {"total_tokens": 20}}),
        )
        assert build_session_stats(events)["context"]["total_tokens"] == 20

    def test_records_last_model(self):
        events = _events(
            (EVENT_USAGE, 0, {"usage": {"input_tokens": 1}, "model": "m1"}),
            (EVENT_USAGE, 10, {"usage": {"input_tokens": 1}, "model": "m2"}),
        )
        assert build_session_stats(events)["model"] == "m2"


def test_events_out_of_order_are_sorted_by_seq():
    events = _events(
        (EVENT_TURN_START, 0, {}),
        (EVENT_STEP_START, 10, {}),
        (EVENT_ASSISTANT_MESSAGE, 20, {}),
        (EVENT_STEP_END, 30, {}),
    )
    shuffled = [events[3], events[1], events[0], events[2]]
    assert build_session_stats(shuffled)["steps"] == 1


def test_malformed_usage_event_is_ignored():
    events = _events(
        (EVENT_USAGE, 0, {"usage": "not-a-dict"}),
        (EVENT_USAGE, 10, {"no_usage_key": 1}),
    )
    stats = build_session_stats(events)
    assert stats["usage"]["total_tokens"] == 0
    assert stats["context"] is None
