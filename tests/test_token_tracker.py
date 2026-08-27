"""Tests for per-step token usage and metrics tag convention."""

from nono_agent.core.types import Usage
from nono_agent.observability.metrics import (
    METRIC_MEMORY_RETRIEVE,
    TAG_AGENT_ID,
    TAG_MODEL,
    TAG_SESSION_ID,
)
from nono_agent.observability.token_tracker import TokenTracker


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
