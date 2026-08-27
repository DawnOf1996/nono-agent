"""指标记录 —— 最小可用的 Counter / Histogram / Gauge。

提供可替换的 MetricsRecorder 协议与内存实现，供引擎/工具记录指标，
也可接入 OpenTelemetry、Prometheus 等后端。
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any, Protocol


class MetricsRecorder(Protocol):
    """指标记录后端协议 —— 定义 Counter/Histogram/Gauge 三种指标类型。"""

    def counter(self, name: str, value: float = 1.0, **tags: str) -> None:
        """递增计数器。"""
        ...

    def histogram(self, name: str, value: float, **tags: str) -> None:
        """记录直方图值。"""
        ...

    def gauge(self, name: str, value: float, **tags: str) -> None:
        """设置仪表盘值。"""
        ...


class NoOpMetricsRecorder:
    """空操作指标记录器（未配置后端时的默认实现）。"""

    def counter(self, name: str, value: float = 1.0, **tags: str) -> None:
        pass

    def histogram(self, name: str, value: float, **tags: str) -> None:
        pass

    def gauge(self, name: str, value: float, **tags: str) -> None:
        pass


class InMemoryMetricsRecorder:
    """内存指标记录器 —— 最小可用实现，支持快照与重置。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._gauges: dict[str, float] = {}

    def _key(self, name: str, tags: dict[str, str]) -> str:
        if not tags:
            return name
        suffix = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{suffix}}}"

    def counter(self, name: str, value: float = 1.0, **tags: str) -> None:
        with self._lock:
            self._counters[self._key(name, tags)] += value

    def histogram(self, name: str, value: float, **tags: str) -> None:
        with self._lock:
            self._histograms[self._key(name, tags)].append(value)

    def gauge(self, name: str, value: float, **tags: str) -> None:
        with self._lock:
            self._gauges[self._key(name, tags)] = value

    def snapshot(self) -> dict[str, Any]:
        """返回所有指标的只读快照。"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "histograms": {k: list(v) for k, v in self._histograms.items()},
                "gauges": dict(self._gauges),
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._gauges.clear()


# 全局指标记录器（默认内存实现；可 set_metrics_recorder 替换为真实后端）
_recorder: MetricsRecorder = InMemoryMetricsRecorder()


def set_metrics_recorder(recorder: MetricsRecorder) -> None:
    """设置全局指标记录器。"""
    global _recorder
    _recorder = recorder


def get_metrics_recorder() -> MetricsRecorder:
    """获取当前指标记录器。"""
    return _recorder


def record_counter(name: str, value: float = 1.0, **tags: str) -> None:
    """便捷递增计数器。"""
    _recorder.counter(name, value, **tags)


def record_histogram(name: str, value: float, **tags: str) -> None:
    """便捷记录直方图。"""
    _recorder.histogram(name, value, **tags)


def record_gauge(name: str, value: float, **tags: str) -> None:
    """便捷设置仪表盘。"""
    _recorder.gauge(name, value, **tags)


# ---------------------------------------------------------------------------
# 预定义指标名称
# ---------------------------------------------------------------------------

# 计数器 —— 累计事件数
METRIC_API_REQUESTS = "nono_agent.api.requests"      # API 请求总数
METRIC_API_ERRORS = "nono_agent.api.errors"           # API 错误总数
METRIC_TOOL_EXECUTIONS = "nono_agent.tool.executions" # 工具执行总数
METRIC_TOOL_ERRORS = "nono_agent.tool.errors"         # 工具错误总数
METRIC_COMPACT_OPERATIONS = "nono_agent.compact.operations"  # 压缩操作总数
METRIC_FALLBACK_TRIGGERED = "nono_agent.fallback.triggered"  # 回退触发次数

# 直方图 —— 耗时分布
METRIC_API_LATENCY = "nono_agent.api.latency_ms"      # API 调用耗时
METRIC_TOOL_LATENCY = "nono_agent.tool.latency_ms"    # 工具执行耗时
METRIC_COMPACT_LATENCY = "nono_agent.compact.latency_ms"  # 压缩操作耗时
METRIC_TURN_TOKENS = "nono_agent.turn.tokens"          # 每轮 token 数
METRIC_TURN_DURATION = "nono_agent.turn.duration_ms"   # 每轮总耗时

# 仪表盘 —— 实时状态值
METRIC_CONTEXT_TOKENS = "nono_agent.context.tokens"   # 当前上下文 token 数
METRIC_MESSAGE_COUNT = "nono_agent.messages.count"     # 当前消息数
METRIC_TOTAL_TOKENS = "nono_agent.tokens.total"        # 当前累计 token 数

# 记忆相关指标（对齐 MemoryManager）
METRIC_MEMORY_RETRIEVE = "nono_agent.memory.retrieve"   # 记忆检索次数
METRIC_MEMORY_WRITE = "nono_agent.memory.write"         # 记忆写入次数
METRIC_MEMORY_REFLECT = "nono_agent.memory.reflect"     # 反思触发次数

# 会话可观测性
METRIC_SESSION_EVENTS = "nono_agent.session.events"      # 写入事件流的事件数
METRIC_SESSION_TURNS = "nono_agent.session.turns"        # 完成的回合数
METRIC_SESSION_STEPS = "nono_agent.session.steps"        # 完成的 step 数
METRIC_PERMISSION_DENIALS = "nono_agent.permission.denials"  # 权限拒绝次数
METRIC_SESSION_DESYNC = "nono_agent.session.desync"      # 日志重建不一致次数

# ---------------------------------------------------------------------------
# 标签约定（tag 名常量）
#
# 所有指标统一使用以下 tag 名，实现「session/agent/model/tool/kind」同源关联，
# 与事件流的 session_id / turn / step / seq 构成可互查的主键体系：
#   record_counter(METRIC_API_REQUESTS, session_id=..., agent_id=..., model=...)
# ---------------------------------------------------------------------------
TAG_SESSION_ID = "session_id"
TAG_AGENT_ID = "agent_id"
TAG_MODEL = "model"
TAG_TOOL = "tool"
TAG_KIND = "kind"
TAG_REASON = "reason"
TAG_EVENT = "event"


# ---------------------------------------------------------------------------
# 事件流 → 指标
#
# 事件流是唯一事实来源，指标是它的聚合投影：同一次运行既能按 seq 逐条回看，
# 也能按标签聚合成曲线。映射表把事件类型翻译成 (指标名, 从载荷取哪些标签)。
# ---------------------------------------------------------------------------
_EVENT_METRICS: dict[str, tuple[str, dict[str, str]]] = {
    # 事件类型 → (指标名, {载荷字段: 标签名})
    "turn/end": (METRIC_SESSION_TURNS, {"reason": TAG_REASON}),
    "step/end": (METRIC_SESSION_STEPS, {"reason": TAG_REASON}),
    "tool/call": (METRIC_TOOL_EXECUTIONS, {"name": TAG_TOOL}),
    "api/retry": (METRIC_API_ERRORS, {"reason": TAG_REASON}),
    "compact/boundary": (METRIC_COMPACT_OPERATIONS, {}),
    "permission/denial": (METRIC_PERMISSION_DENIALS, {"tool": TAG_TOOL}),
    "memory/write": (METRIC_MEMORY_WRITE, {"kind": TAG_KIND}),
    "memory/retrieve": (METRIC_MEMORY_RETRIEVE, {}),
}


def record_event_metric(
    event_type: str, data: dict[str, Any], *, session_id: str
) -> None:
    """把一条会话事件投影为指标。

    每条事件都计入 ``METRIC_SESSION_EVENTS``（按事件类型分标签），映射表命中的
    事件类型再额外计入对应的领域指标。载荷里缺失的标签取 ``unknown``，
    保证同一指标的标签集稳定，聚合后端不会因维度漂移而分裂时间序列。

    Args:
        event_type: 事件类型（``session.events`` 的 ``EVENT_*`` 常量值）。
        data: 事件载荷。
        session_id: 会话 ID，作为所有指标的公共标签。
    """
    record_counter(
        METRIC_SESSION_EVENTS,
        1.0,
        **{TAG_SESSION_ID: session_id, TAG_EVENT: event_type},
    )

    mapping = _EVENT_METRICS.get(event_type)
    if mapping is None:
        return

    metric_name, tag_sources = mapping
    tags = {TAG_SESSION_ID: session_id}
    for payload_key, tag_name in tag_sources.items():
        tags[tag_name] = str(data.get(payload_key, "unknown"))
    record_counter(metric_name, 1.0, **tags)
