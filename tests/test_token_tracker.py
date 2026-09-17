"""Tests for per-step token usage and metrics tag convention."""

from nono_agent.core.types import Usage
from nono_agent.observability.metrics import (
    METRIC_MEMORY_RETRIEVE,
    TAG_AGENT_ID,
    TAG_MODEL,
    TAG_SESSION_ID,
)
from nono_agent.observability.token_tracker import (
    TokenTracker,
    context_usage_color,
    format_context_usage,
)


def _sample_context_usage(total: int = 45_210) -> dict:
    return {
        "context_window": 196_608,
        "total_tokens": total,
        "usage_ratio": round(total / 196_608, 4),
        "remaining_tokens": 196_608 - total,
        "system_prompt_tokens": 2_150,
        "system_prompt_ratio": 0.0109,
        "tools_tokens": 3_400,
        "tools_count": 12,
        "tools_ratio": 0.0173,
        "messages_tokens": total - 2_150 - 3_400,
        "messages_ratio": 0.2,
        "message_count": 18,
        "is_auto_compact_threshold": False,
        "is_at_blocking_limit": False,
    }


class TestTokenTrackerSteps:
    def test_step_usages_recorded(self):
        tt = TokenTracker()
        tt.record_usage(Usage(input_tokens=10, output_tokens=5), model="m1")
        tt.record_usage(Usage(input_tokens=20, output_tokens=8), model="m1")

        steps = tt.get_step_usages()
        assert len(steps) == 2
        assert steps[0]["input_tokens"] == 10
        assert steps[0]["output_tokens"] == 5
        assert steps[1]["total_tokens"] == 28

    def test_reset_clears_steps(self):
        tt = TokenTracker()
        tt.record_usage(Usage(input_tokens=10, output_tokens=5), model="m1")
        tt.reset()
        assert tt.get_step_usages() == []
        assert tt.get_total_tokens() == 0


class TestMetricsTagConvention:
    def test_tag_constants_exist(self):
        assert TAG_SESSION_ID == "session_id"
        assert TAG_AGENT_ID == "agent_id"
        assert TAG_MODEL == "model"

    def test_memory_metric_names(self):
        assert METRIC_MEMORY_RETRIEVE == "nono_agent.memory.retrieve"


class TestContextUsageTracking:
    def test_no_context_usage_initially(self):
        assert TokenTracker().get_context_usage() is None
        assert TokenTracker().get_context_usage_history() == []

    def test_record_and_get_latest(self):
        tt = TokenTracker()
        tt.record_context_usage(_sample_context_usage(total=100))
        tt.record_context_usage(_sample_context_usage(total=200))
        assert tt.get_context_usage()["total_tokens"] == 200

    def test_history_preserves_order(self):
        tt = TokenTracker()
        for total in (100, 200, 300):
            tt.record_context_usage(_sample_context_usage(total=total))
        history = tt.get_context_usage_history()
        assert [h["total_tokens"] for h in history] == [100, 200, 300]

    def test_record_stores_a_copy(self):
        tt = TokenTracker()
        snapshot = _sample_context_usage(total=100)
        tt.record_context_usage(snapshot)
        snapshot["total_tokens"] = 999
        assert tt.get_context_usage()["total_tokens"] == 100

    def test_reset_clears_context_usage(self):
        tt = TokenTracker()
        tt.record_context_usage(_sample_context_usage())
        tt.reset()
        assert tt.get_context_usage() is None
        assert tt.get_context_usage_history() == []


class TestFormatContextUsage:
    def test_none_renders_none(self):
        assert format_context_usage(None) is None
        assert format_context_usage({}) is None

    def test_line_contains_total_and_breakdown(self):
        line = format_context_usage(_sample_context_usage())
        assert line is not None
        assert "Context: 45,210/196,608 tokens (23.0%)" in line
        assert "system 2,150 (1.1%)" in line
        assert "tools 3,400 (1.7%)" in line
        assert "messages 39,660 (20.0%)" in line
        assert "█" not in line and "░" not in line  # 不渲染进度条

    def test_color_thresholds(self):
        assert context_usage_color(0.10) == "green"
        assert context_usage_color(0.49) == "green"
        assert context_usage_color(0.50) == "yellow"
        assert context_usage_color(0.79) == "yellow"
        assert context_usage_color(0.80) == "red"
        assert context_usage_color(0.99) == "red"
