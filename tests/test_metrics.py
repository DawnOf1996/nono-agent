"""Tests for the in-memory metrics recorder."""

from nono_agent.observability.metrics import (
    METRIC_TOOL_EXECUTIONS,
    InMemoryMetricsRecorder,
)


class TestInMemoryMetricsRecorder:
    def test_counter(self):
        r = InMemoryMetricsRecorder()
        r.counter(METRIC_TOOL_EXECUTIONS, tool="bash")
        r.counter(METRIC_TOOL_EXECUTIONS, tool="bash")
        snap = r.snapshot()
        assert snap["counters"]["nono_agent.tool.executions{tool=bash}"] == 2

    def test_histogram(self):
        r = InMemoryMetricsRecorder()
        r.histogram("latency", 10.0)
        r.histogram("latency", 20.0)
        snap = r.snapshot()
        assert snap["histograms"]["latency"] == [10.0, 20.0]

    def test_gauge_and_reset(self):
        r = InMemoryMetricsRecorder()
        r.gauge("cost", 1.5)
        assert r.snapshot()["gauges"]["cost"] == 1.5
        r.reset()
        assert r.snapshot()["gauges"] == {}
