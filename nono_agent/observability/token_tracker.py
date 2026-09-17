"""多维度 Token 跟踪。

跟踪 token 用量（输入 / 输出 / 缓存命中读取 / 缓存创建）、API 调用耗时、
工具执行耗时，以及按模型分解的用量明细。
不再追踪价格（USD）；仅统计 token 数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nono_agent.core.types import Usage


@dataclass
class ModelTokenUsage:
    """单个模型的 Token 用量统计。"""

    input_tokens: int = 0            # 输入 token 数
    output_tokens: int = 0           # 输出 token 数
    cache_read_tokens: int = 0       # 缓存命中（读取）token 数
    cache_creation_tokens: int = 0   # 缓存创建 token 数
    api_duration_ms: float = 0       # API 调用累计耗时（毫秒）
    num_requests: int = 0            # API 请求次数

    @property
    def total_tokens(self) -> int:
        """所有类型的 token 总和。"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


# ---------------------------------------------------------------------------
# 上下文用量渲染辅助 —— 供 CLI TUI 与 /tokens 命令共用
# ---------------------------------------------------------------------------


def format_context_usage(usage: dict[str, Any] | None) -> str | None:
    """把上下文用量分析快照渲染成单行摘要（纯文本，总量/占比 + 三分项）。

    输入是 ``analyze_context_usage()`` 的返回结构；None/空返回 None。
    """
    if not usage:
        return None
    ratio = float(usage.get("usage_ratio", 0.0))
    return (
        f"Context: {usage.get('total_tokens', 0):,}/"
        f"{usage.get('context_window', 0):,} tokens ({ratio * 100:.1f}%) "
        f"system {usage.get('system_prompt_tokens', 0):,} "
        f"({usage.get('system_prompt_ratio', 0.0) * 100:.1f}%) | "
        f"tools {usage.get('tools_tokens', 0):,} "
        f"({usage.get('tools_ratio', 0.0) * 100:.1f}%) | "
        f"messages {usage.get('messages_tokens', 0):,} "
        f"({usage.get('messages_ratio', 0.0) * 100:.1f}%)"
    )


def context_usage_color(ratio: float) -> str:
    """按窗口占用率给出 rich 颜色 —— <50% 绿 / <80% 黄 / ≥80% 红。"""
    if ratio < 0.50:
        return "green"
    if ratio < 0.80:
        return "yellow"
    return "red"


@dataclass
class TokenTracker:
    """跟踪整个会话的累计 Token 用量。

    支持：
    - 按类型统计 token（输入 / 输出 / 缓存命中读取 / 缓存创建）
    - API 调用耗时
    - 工具执行耗时
    - 按模型分解的用量明细
    """

    # 累计 token 用量
    total_usage: Usage = field(default_factory=Usage)

    # 累计 API 调用耗时（毫秒）
    total_api_duration_ms: float = 0.0

    # 累计工具执行耗时（毫秒）
    total_tool_duration_ms: float = 0.0

    # 按模型分解的用量明细
    _model_usage: dict[str, ModelTokenUsage] = field(default_factory=dict)

    # 每步（每次 record_usage）的用量记录 —— 支持「下钻到 step」
    _step_usages: list[dict[str, Any]] = field(default_factory=list)

    # 最近一次上下文用量分析快照（analyze_context_usage 口径，含 system/tools/messages 分项）
    _latest_context_usage: dict[str, Any] | None = None

    # 上下文用量快照历史 —— 每次 record_context_usage 追加一条，可回看占用趋势
    _context_usage_history: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 记录方法 —— 累加每次 API 响应的用量
    # ------------------------------------------------------------------

    def record_usage(self, usage: Usage, model: str = "unknown") -> None:
        """记录一次 API 响应的 token 用量。"""
        self.total_usage = self.total_usage + usage

        if model not in self._model_usage:
            self._model_usage[model] = ModelTokenUsage()
        mu = self._model_usage[model]
        mu.input_tokens += usage.input_tokens
        mu.output_tokens += usage.output_tokens
        mu.cache_read_tokens += usage.cache_read_input_tokens
        mu.cache_creation_tokens += usage.cache_creation_input_tokens
        mu.num_requests += 1

        self._step_usages.append(
            {
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_input_tokens,
                "cache_creation_tokens": usage.cache_creation_input_tokens,
                "total_tokens": usage.total_tokens,
            }
        )

    def record_api_duration(self, duration_ms: float, model: str = "unknown") -> None:
        """记录 API 调用耗时。"""
        self.total_api_duration_ms += duration_ms
        if model in self._model_usage:
            self._model_usage[model].api_duration_ms += duration_ms

    def record_context_usage(self, usage: dict[str, Any]) -> None:
        """记录一次上下文用量分析快照（总量/占比 + system/tools/messages 分项）。

        入参为 ``analyze_context_usage()`` 的返回结构；内部保存浅拷贝，
        避免调用方后续修改快照影响历史记录。
        """
        snapshot = dict(usage)
        self._latest_context_usage = snapshot
        self._context_usage_history.append(snapshot)

    def record_tool_duration(self, duration_ms: float) -> None:
        """记录工具执行耗时。"""
        self.total_tool_duration_ms += duration_ms

    # ------------------------------------------------------------------
    # 查询方法 —— 获取累计统计数据
    # ------------------------------------------------------------------

    def get_total_usage(self) -> Usage:
        """获取累计 token 用量。"""
        return self.total_usage

    def get_total_input_tokens(self) -> int:
        """累计输入 token 数。"""
        return self.total_usage.input_tokens

    def get_total_output_tokens(self) -> int:
        """累计输出 token 数。"""
        return self.total_usage.output_tokens

    def get_cache_read_tokens(self) -> int:
        """累计缓存命中（读取）token 数。"""
        return self.total_usage.cache_read_input_tokens

    def get_cache_creation_tokens(self) -> int:
        """累计缓存创建 token 数。"""
        return self.total_usage.cache_creation_input_tokens

    def get_total_tokens(self) -> int:
        """所有类型的 token 总和。"""
        return self.total_usage.total_tokens

    def get_total_api_duration(self) -> float:
        """获取累计 API 调用耗时（毫秒）。"""
        return self.total_api_duration_ms

    def get_total_tool_duration(self) -> float:
        """获取累计工具执行耗时（毫秒）。"""
        return self.total_tool_duration_ms

    def get_model_usage(self) -> dict[str, dict[str, Any]]:
        """获取按模型分解的用量明细。"""
        return {
            model: {
                "input_tokens": mu.input_tokens,
                "output_tokens": mu.output_tokens,
                "cache_read_tokens": mu.cache_read_tokens,
                "cache_creation_tokens": mu.cache_creation_tokens,
                "total_tokens": mu.total_tokens,
                "api_duration_ms": mu.api_duration_ms,
                "num_requests": mu.num_requests,
            }
            for model, mu in self._model_usage.items()
        }

    def get_step_usages(self) -> list[dict[str, Any]]:
        """获取每步用量记录（下钻到 step）。"""
        return list(self._step_usages)

    def get_context_usage(self) -> dict[str, Any] | None:
        """获取最近一次上下文用量分析快照（无记录时返回 None）。"""
        return self._latest_context_usage

    def get_context_usage_history(self) -> list[dict[str, Any]]:
        """获取上下文用量快照历史（按记录顺序）。"""
        return list(self._context_usage_history)

    def reset(self) -> None:
        """重置所有跟踪数据。"""
        self.total_usage = Usage()
        self.total_api_duration_ms = 0.0
        self.total_tool_duration_ms = 0.0
        self._model_usage.clear()
        self._step_usages.clear()
        self._latest_context_usage = None
        self._context_usage_history.clear()
