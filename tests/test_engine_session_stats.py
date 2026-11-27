"""引擎边写边累加的会话统计。

重点是「活跃会话每步都能 O(1) 取到快照」，且与整条日志重算口径一致。
"""

from __future__ import annotations

import pytest

from nono_agent.session.events import (
    EVENT_ASSISTANT_CHUNK,
    EVENT_STEP_END,
    EVENT_STEP_START,
    EVENT_TURN_START,
    EVENT_USAGE,
    SessionEvent,
)
from nono_agent.session.stats import StatsAccumulator, build_session_stats
from tests.helpers import create_test_engine


def _event(seq: int, type_: str, ts: float, **data):
    return SessionEvent(seq=seq, type=type_, data=data, timestamp=ts)


def _feed_all(events: list[SessionEvent]) -> StatsAccumulator:
    acc = StatsAccumulator()
    acc.feed_all(events)
    return acc


def _usage(inp: int, out: int, read: int = 0, create: int = 0) -> dict:
    return {
        "usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": create,
        },
        "model": "m",
    }


def _two_step_stream() -> list[SessionEvent]:
    return [
        _event(1, EVENT_TURN_START, 0.0),
        _event(2, EVENT_STEP_START, 100.0),
        _event(3, EVENT_ASSISTANT_CHUNK, 200.0, chunk={}),
        _event(4, EVENT_ASSISTANT_CHUNK, 400.0, chunk={}),
        _event(5, EVENT_STEP_END, 900.0),
        _event(6, EVENT_USAGE, 900.0, **_usage(10, 20, 100)),
        _event(7, EVENT_STEP_START, 1000.0),
        _event(8, EVENT_ASSISTANT_CHUNK, 1200.0, chunk={}),
        _event(9, EVENT_STEP_END, 1500.0),
        _event(10, EVENT_USAGE, 1500.0, **_usage(5, 8, 130)),
    ]


class TestIncrementalMatchesBatch:
    def test_step_by_step_equals_whole_stream(self):
        events = _two_step_stream()
        assert build_session_stats(events) == _feed_all(events).snapshot()

    def test_feeding_one_by_one_equals_feeding_bulk(self):
        events = _two_step_stream()
        bulk = _feed_all(events).snapshot()
        once = StatsAccumulator()
        for event in events:
            once.feed(event)
        assert once.snapshot() == bulk

    def test_out_of_order_input_is_sorted(self):
        events = _two_step_stream()
        assert build_session_stats(list(reversed(events))) == build_session_stats(events)


class TestSnapshotDuringStream:
    def test_snapshot_does_not_consume_open_step(self):
        # 关键回归：引擎在 step 还在跑时就会取快照。若 snapshot 把进行中的
        # step 真收掉，等真正的 step/end 到达时这段时长就被漏记了。
        events = _two_step_stream()
        acc = StatsAccumulator()
        for event in events[:5]:  # 到第一个 step/end 为止
            acc.feed(event)
        acc.feed(events[6])  # step/start（第二个 step 开始）
        acc.feed(events[7])  # 首条内容

        before = acc.snapshot()
        acc.snapshot()
        acc.snapshot()  # 反复取不应改变任何东西
        assert acc.snapshot() == before

        acc.feed(events[8])  # 真正的 step/end
        acc.feed(events[9])  # usage
        after = acc.snapshot()

        # 第二个 step 的耗时只应被记一次。
        assert after["tool_duration_ms"] == before["tool_duration_ms"] + 300.0

    def test_partial_stream_grows_monotonically(self):
        events = _two_step_stream()
        acc = StatsAccumulator()
        steps_seen = []
        for event in events:
            acc.feed(event)
            steps_seen.append(acc.snapshot()["steps"])
        assert steps_seen == sorted(steps_seen)
        assert steps_seen[-1] == 2

    def test_reset_clears_everything(self):
        acc = _feed_all(_two_step_stream())
        assert acc.snapshot()["steps"] == 2
        acc.reset()
        fresh = acc.snapshot()
        assert fresh["steps"] == 0
        assert fresh["usage"]["total_tokens"] == 0
        assert fresh["tps"] is None


@pytest.mark.asyncio
class TestEngineStats:
    async def test_engine_exposes_stats(self):
        engine = create_test_engine()
        try:
            assert engine.session_stats()["steps"] == 0
            assert engine.session_stats()["usage"]["total_tokens"] == 0
        finally:
            await engine.close()
