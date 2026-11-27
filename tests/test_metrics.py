"""Tests for the in-memory metrics recorder and event-to-metric projection."""

from nono_agent.observability.metrics import (
    METRIC_API_ERRORS,
    METRIC_COMPACT_OPERATIONS,
    METRIC_MEMORY_WRITE,
    METRIC_PERMISSION_DENIALS,
    METRIC_SESSION_EVENTS,
    METRIC_SESSION_STEPS,
    METRIC_SESSION_TURNS,
    METRIC_TOOL_EXECUTIONS,
    InMemoryMetricsRecorder,
    record_event_metric,
    set_metrics_recorder,
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


class TestRecordEventMetric:
    def setup_method(self):
        self.recorder = InMemoryMetricsRecorder()
        set_metrics_recorder(self.recorder)

    def teardown_method(self):
        set_metrics_recorder(InMemoryMetricsRecorder())

    def _counters(self):
        return self.recorder.snapshot()["counters"]

    def test_every_event_increments_session_events(self):
        record_event_metric("user/message", {"content": "hi"}, session_id="s1")
        key = f"{METRIC_SESSION_EVENTS}{{event=user/message,session_id=s1}}"
        assert self._counters()[key] == 1

    def test_unmapped_type_does_not_invent_a_domain_metric(self):
        record_event_metric("request/header", {"reason": "initial"}, session_id="s1")
        counters = self._counters()
        assert any(k.startswith(METRIC_SESSION_EVENTS) for k in counters)
        assert not any(k.startswith(METRIC_TOOL_EXECUTIONS) for k in counters)

    def test_mapped_events_carry_stable_tags(self):
        record_event_metric("turn/end", {"reason": "COMPLETED"}, session_id="s1")
        record_event_metric("step/end", {"reason": "next_turn"}, session_id="s1")
        record_event_metric("tool/call", {"name": "bash", "args": {}}, session_id="s1")
        record_event_metric("api/retry", {"reason": "fallback_model"}, session_id="s1")
        record_event_metric("compact/boundary", {"kept": []}, session_id="s1")
        record_event_metric("permission/denial", {"tool": "bash"}, session_id="s1")
        record_event_metric("memory/write", {"kind": "semantic"}, session_id="s1")

        counters = self._counters()
        assert counters[f"{METRIC_SESSION_TURNS}{{reason=COMPLETED,session_id=s1}}"] == 1
        assert counters[f"{METRIC_SESSION_STEPS}{{reason=next_turn,session_id=s1}}"] == 1
        assert counters[f"{METRIC_TOOL_EXECUTIONS}{{session_id=s1,tool=bash}}"] == 1
        assert counters[f"{METRIC_API_ERRORS}{{reason=fallback_model,session_id=s1}}"] == 1
        assert counters[f"{METRIC_COMPACT_OPERATIONS}{{session_id=s1}}"] == 1
        assert counters[f"{METRIC_PERMISSION_DENIALS}{{session_id=s1,tool=bash}}"] == 1
        assert counters[f"{METRIC_MEMORY_WRITE}{{kind=semantic,session_id=s1}}"] == 1

    def test_missing_payload_tag_falls_back_to_unknown(self):
        record_event_metric("tool/call", {}, session_id="s1")
        key = f"{METRIC_TOOL_EXECUTIONS}{{session_id=s1,tool=unknown}}"
        assert self._counters()[key] == 1
