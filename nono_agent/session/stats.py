"""会话级统计 —— 由事件流推导，不依赖引擎的内存态。

为什么从事件流算而不是读 ``TokenTracker``：那个 tracker 的 api/tool 计时器
从来没被 ``record`` 过（``result`` 帧里的 ``duration_api_ms`` 恒为 0），
TTFT 和 TPS 则完全没有采集。事件流是唯一在「重开历史会话」后依然完整的
数据源 —— 只要它落盘，统计就落盘。

时间按每个 step 三段切：

``step ─┬─ 等首 token ─┬───── 生成中 ──────┬── 工具执行 ──┬─``
``      └── TTFT ─────┘    └── model_ms ───┘   └ tool_ms ─┘``

前两段之和等于 step 总时长，不重不漏。

两种用法：

* ``build_session_stats(events)`` —— 一次性折叠整条日志（历史会话）。
* ``StatsAccumulator`` —— 引擎边写事件边累加，每步之后 ``snapshot()`` 直接出结果。
  整份日志重读一次要几百毫秒（5 万条事件约 0.7s）且随会话增长，跑在每步刷新上
  太贵；累加器是 O(1)，两条路走的是同一份实现，不会出现两套口径。
"""

from __future__ import annotations

from typing import Any

from nono_agent.session.events import (
    EVENT_ASSISTANT_CHUNK,
    EVENT_ASSISTANT_MESSAGE,
    EVENT_STEP_END,
    EVENT_STEP_START,
    EVENT_TURN_START,
    EVENT_USAGE,
    SessionEvent,
)

# 上下文窗口兜底值，与 utils/tokens.py 的 DEFAULT_CONTEXT_WINDOW 保持一致。
DEFAULT_CONTEXT_WINDOW = 200_000

_BUCKETS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _usage_of(data: dict[str, Any]) -> dict[str, int]:
    payload = data.get("usage")
    if not isinstance(payload, dict):
        return {}
    out: dict[str, int] = {}
    for key in _BUCKETS:
        value = payload.get(key)
        out[key] = int(value) if isinstance(value, (int, float)) else 0
    return out


def _context_of(data: dict[str, Any]) -> dict[str, Any] | None:
    payload = data.get("context")
    return payload if isinstance(payload, dict) else None


class StatsAccumulator:
    """可增量喂入的事件流统计器。

    只依赖事件的**顺序**，不依赖 seq —— 引擎在 ``_flush_events()`` 成功落盘后
    才喂进来，那时 seq 已分配、顺序已定。喂入点在落盘之后是必须的：
    ``_flush_events`` 失败会把待写事件放回队首重试，若在入队时就喂会被算两遍。
    """

    __slots__ = (
        "turns",
        "steps",
        "model_ms",
        "tool_ms",
        "generate_ms",
        "ttft_total_ms",
        "ttft_samples",
        "model",
        "last_context",
        "last_input_total",
        "last_timestamp",
        "step_started_at",
        "first_content_at",
        "last_content_at",
        "content_events",
        "totals",
        "_step_open",
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """清空所有累加状态。"""
        self.totals: dict[str, int] = dict.fromkeys(_BUCKETS, 0)
        self.turns = 0
        self.steps = 0
        self.model_ms = 0.0
        self.tool_ms = 0.0
        self.generate_ms = 0.0
        self.ttft_total_ms = 0.0
        self.ttft_samples = 0
        self.model: str | None = None
        self.last_context: dict[str, Any] | None = None
        self.last_input_total = 0
        self.last_timestamp = 0.0
        self.step_started_at: float | None = None
        self.first_content_at: float | None = None
        self.last_content_at: float | None = None
        self.content_events = 0
        self._step_open = False

    # -- 喂入 ---------------------------------------------------------------

    def feed(self, event: SessionEvent) -> None:
        """喂入一条事件（按事件流顺序）。"""
        self.last_timestamp = event.timestamp
        if event.type == EVENT_TURN_START:
            self.turns += 1
        elif event.type == EVENT_STEP_START:
            self.steps += 1
            self.step_started_at = event.timestamp
            self.first_content_at = None
            self.last_content_at = None
            self.content_events = 0
            self._step_open = True
        elif event.type in (EVENT_ASSISTANT_CHUNK, EVENT_ASSISTANT_MESSAGE):
            if self.first_content_at is None:
                self.first_content_at = event.timestamp
            self.last_content_at = event.timestamp
            self.content_events += 1
        elif event.type == EVENT_STEP_END:
            self.close_step(event.timestamp)
        elif event.type == EVENT_USAGE:
            self._feed_usage(event)

    def feed_all(self, events: list[SessionEvent]) -> None:
        """按 seq 升序喂入整条事件流。"""
        for event in sorted(events, key=lambda e: e.seq):
            self.feed(event)

    def _feed_usage(self, event: SessionEvent) -> None:
        usage = _usage_of(event.data)
        if not usage:
            return
        for key in _BUCKETS:
            self.totals[key] += usage[key]
        candidate = event.data.get("model")
        if isinstance(candidate, str) and candidate:
            self.model = candidate
        context = _context_of(event.data)
        if context is not None:
            self.last_context = context
        # 旧版本日志没有 context 字段时，用最后一次请求实际带进去的
        # 输入量兜底 —— 每轮都整段重发上下文，这个数就是当前上下文大小。
        self.last_input_total = (
            usage["input_tokens"]
            + usage["cache_read_input_tokens"]
            + usage["cache_creation_input_tokens"]
        )

    def close_step(self, ended_at: float) -> None:
        """收到 ``step/end`` 时把三段时长折进累加器（会改动状态）。"""
        started = self.step_started_at
        first = self.first_content_at
        last = self.last_content_at
        if started is not None and first is not None and last is not None:
            # 请求就失败/被取消的 step 没有任何内容事件，不计入时长，
            # 否则这些空转的时间会白拉长统计。
            self.ttft_total_ms += first - started
            self.ttft_samples += 1
            self.model_ms += max(0.0, last - started)
            self.tool_ms += max(0.0, ended_at - last)
            # 只有落过多个内容事件（流式分片）才量得出「首 token 之后还在生成多久」。
            # 只有一条 assistant/message 时首尾同一刻，这一步的生成耗时无从得知 ——
            # 此时宁可不报 TPS，也不要把后面的工具执行时间算成生成时间。
            if self.content_events > 1:
                self.generate_ms += last - first
        self.step_started_at = None
        self.first_content_at = None
        self.last_content_at = None
        self.content_events = 0
        self._step_open = False

    # -- 读出 ---------------------------------------------------------------

    def _measure(self, pending_end: float | None) -> tuple[float, float, float, float, int]:
        """算出当前累计量。``pending_end`` 非空时把「进行中的 step」也按它收尾计入。

        这是纯计算、不改状态 —— snapshot() 要能在 step 还在跑时被调用，
        真去 close 掉的话，等真正的 step/end 到达时这段就被漏记了。
        """
        model_ms, tool_ms, generate_ms = self.model_ms, self.tool_ms, self.generate_ms
        ttft_total, ttft_samples = self.ttft_total_ms, self.ttft_samples
        started = self.step_started_at
        first, last = self.first_content_at, self.last_content_at
        if (
            pending_end is not None
            and started is not None
            and first is not None
            and last is not None
        ):
            ttft_total += first - started
            ttft_samples += 1
            model_ms += max(0.0, last - started)
            tool_ms += max(0.0, pending_end - last)
            if self.content_events > 1:
                generate_ms += last - first
        return model_ms, tool_ms, generate_ms, ttft_total, ttft_samples

    def snapshot(self) -> dict[str, Any]:
        """当前累计统计。进行中的 step 按当前时刻**预估**计入，不消费它。"""
        pending_end = None
        if self._step_open and self.step_started_at is not None:
            pending_end = max(self.last_timestamp, self.step_started_at)
        model_ms, tool_ms, generate_ms, ttft_total, ttft_samples = self._measure(pending_end)

        total_tokens = sum(self.totals.values())
        read = self.totals["cache_read_input_tokens"]
        fresh = self.totals["input_tokens"]
        # 分母不含 cache_creation：那是「写」不是「命中」，算进去会低估命中率。
        denominator = read + fresh
        cache_hit_ratio = (read / denominator) if denominator > 0 else None
        # TPS 只算「首 token 之后仍在生成」的那段时间；generate_ms 为 0 说明这轮
        # 没有流式分片可量，宁可不报，也不要拿工具执行时间去凑一个假的吞吐。
        tps = (self.totals["output_tokens"] / generate_ms * 1000.0) if generate_ms > 0 else None

        return {
            "turns": self.turns,
            "steps": self.steps,
            "model_duration_ms": round(model_ms, 3),
            "tool_duration_ms": round(tool_ms, 3),
            "ttft_ms": round(ttft_total / ttft_samples, 3) if ttft_samples else None,
            "tps": round(tps, 2) if tps is not None else None,
            "model": self.model,
            "usage": {
                **self.totals,
                "total_tokens": total_tokens,
                "cache_hit_ratio": (
                    round(cache_hit_ratio, 4) if cache_hit_ratio is not None else None
                ),
            },
            "context": _normalize_context(self.last_context, self.last_input_total),
        }


def build_session_stats(events: list[SessionEvent]) -> dict[str, Any]:
    """把一条事件流折叠成会话级统计。

    Args:
        events: 会话的全部事件（内部按 seq 排序，调用方无需预排序）。

    Returns:
        可直接序列化的统计字典，字段含义见 ``SessionStats`` 协议类型。
    """
    accumulator = StatsAccumulator()
    accumulator.feed_all(events)
    return accumulator.snapshot()


def _normalize_context(
    context: dict[str, Any] | None,
    fallback_total: int,
) -> dict[str, Any] | None:
    """把引擎写的上下文快照规整成稳定形状，缺失时按最后一次输入量兜底。"""
    if context is None:
        if fallback_total <= 0:
            return None
        window = DEFAULT_CONTEXT_WINDOW
        return {
            "context_window": window,
            "total_tokens": fallback_total,
            "usage_ratio": round(min(1.0, fallback_total / window), 4) if window else 0.0,
            "system_prompt_tokens": None,
            "tools_tokens": None,
            "messages_tokens": None,
            "message_count": None,
        }

    window = int(context.get("context_window") or 0) or DEFAULT_CONTEXT_WINDOW
    total = int(context.get("total_tokens") or 0)
    return {
        "context_window": window,
        "total_tokens": total,
        "usage_ratio": float(context.get("usage_ratio") or 0.0),
        "system_prompt_tokens": _optional_int(context.get("system_prompt_tokens")),
        "tools_tokens": _optional_int(context.get("tools_tokens")),
        "messages_tokens": _optional_int(context.get("messages_tokens")),
        "message_count": _optional_int(context.get("message_count")),
    }


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


__all__ = ["DEFAULT_CONTEXT_WINDOW", "StatsAccumulator", "build_session_stats"]
