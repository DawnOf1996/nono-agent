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
