"""OpenTelemetry 指标集成（预留接口）。

提供可接入 OpenTelemetry、Prometheus 或其他后端的指标抽象层。
当前为 No-Op 占位实现。
"""

from __future__ import annotations

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


# 全局指标记录器（生产环境中替换为真实后端）
_recorder: MetricsRecorder = NoOpMetricsRecorder()


def set_metrics_recorder(recorder: MetricsRecorder) -> None:
    """设置全局指标记录器。"""
    global _recorder
    _recorder = recorder


def get_metrics_recorder() -> MetricsRecorder:
    """获取当前指标记录器。"""
    return _recorder


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
METRIC_COST_USD = "nono_agent.cost.usd"               # 当前累计成本
