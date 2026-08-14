"""多维度成本跟踪。

跟踪 token 用量（input/output/cache_read/cache_creation）、USD 成本、
API 调用耗时、工具执行耗时以及按模型分解的用量明细。
对应 TS 版的 cost-tracker.ts。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nono_agent.core.types import Usage


@dataclass
class ModelUsage:
    """单个模型的用量统计。"""

    input_tokens: int = 0            # 输入 token 数
    output_tokens: int = 0           # 输出 token 数
    cache_read_tokens: int = 0       # 缓存读取 token 数
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


@dataclass
class CostTracker:
    """跟踪整个会话的累计成本。

    支持：
    - 按类型统计 token 用量（input/output/cache_read/cache_creation）
    - USD 成本估算
    - API 调用耗时
    - 工具执行耗时
    - 按模型分解的用量明细
    """

    # 累计 token 用量
    total_usage: Usage = field(default_factory=Usage)

    # 累计 USD 成本
    total_cost_usd: float = 0.0

    # 累计 API 调用耗时（毫秒）
    total_api_duration_ms: float = 0.0

    # 累计工具执行耗时（毫秒）
    total_tool_duration_ms: float = 0.0

    # 按模型分解的用量明细
    _model_usage: dict[str, ModelUsage] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 记录方法 —— 累加每次 API 响应的用量
    # ------------------------------------------------------------------

    def record_usage(self, usage: Usage, model: str = "unknown") -> None:
        """记录一次 API 响应的 token 用量。"""
        self.total_usage = self.total_usage + usage

        if model not in self._model_usage:
            self._model_usage[model] = ModelUsage()
        mu = self._model_usage[model]
        mu.input_tokens += usage.input_tokens
        mu.output_tokens += usage.output_tokens
        mu.cache_read_tokens += usage.cache_read_input_tokens
        mu.cache_creation_tokens += usage.cache_creation_input_tokens
        mu.num_requests += 1

    def record_api_duration(self, duration_ms: float, model: str = "unknown") -> None:
        """记录 API 调用耗时。"""
        self.total_api_duration_ms += duration_ms
        if model in self._model_usage:
            self._model_usage[model].api_duration_ms += duration_ms

    def record_tool_duration(self, duration_ms: float) -> None:
        """记录工具执行耗时。"""
        self.total_tool_duration_ms += duration_ms

    def record_cost(self, cost_usd: float) -> None:
        """记录 USD 成本。"""
        self.total_cost_usd += cost_usd

    # ------------------------------------------------------------------
    # 查询方法 —— 获取累计统计数据
    # ------------------------------------------------------------------

    def get_total_cost(self) -> float:
        """获取累计 USD 成本。"""
        return self.total_cost_usd

    def get_total_api_duration(self) -> float:
        """获取累计 API 调用耗时（毫秒）。"""
        return self.total_api_duration_ms

    def get_total_usage(self) -> Usage:
        """获取累计 token 用量。"""
        return self.total_usage

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

    def check_budget(self, max_budget_usd: float | None) -> bool:
        """检查是否超出预算。

        Returns:
            True 表示在预算内，False 表示已超出
        """
        if max_budget_usd is None:
            return True
        return self.total_cost_usd < max_budget_usd

    def reset(self) -> None:
        """重置所有跟踪数据。"""
        self.total_usage = Usage()
        self.total_cost_usd = 0.0
        self.total_api_duration_ms = 0.0
        self.total_tool_duration_ms = 0.0
        self._model_usage.clear()


# ---------------------------------------------------------------------------
# 成本估算 —— 按模型定价表计算 USD 成本
# ---------------------------------------------------------------------------

# Anthropic Claude 每百万 token 定价（USD）
ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "claude-3-5-sonnet": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "claude-3-5-haiku": {
        "input": 0.80,
        "output": 4.0,
        "cache_read": 0.08,
        "cache_creation": 1.0,
    },
}

# OpenAI 每百万 token 定价（USD）
OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {
        "input": 2.50,
        "output": 10.0,
        "cache_read": 1.25,
        "cache_creation": 0.0,
    },
    "o1": {
        "input": 15.0,
        "output": 60.0,
        "cache_read": 7.50,
        "cache_creation": 0.0,
    },
}

# MiniMax 每百万 token 定价（USD）
MINIMAX_PRICING: dict[str, dict[str, float]] = {
    "MiniMax-M3": {
        "input": 0.50,
        "output": 2.0,
        "cache_read": 0.0,
        "cache_creation": 0.0,
    },
}

# 所有定价表合并
_ALL_PRICING: dict[str, dict[str, float]] = {
    **ANTHROPIC_PRICING,
    **OPENAI_PRICING,
    **MINIMAX_PRICING,
}


def estimate_cost(usage: Usage, model: str) -> float:
    """估算单次 API 响应的 USD 成本。

    根据模型名称匹配定价表，按 token 类型分别计费。
    未知模型返回 0.0（不报错）。
    """
    # 查找匹配的模型定价（精确匹配优先，然后前缀匹配）
    pricing = _ALL_PRICING.get(model)
    if pricing is None:
        for prefix, p in _ALL_PRICING.items():
            if model.startswith(prefix):
                pricing = p
                break

    if pricing is None:
        return 0.0

    cost = (
        usage.input_tokens * pricing["input"] / 1_000_000
        + usage.output_tokens * pricing["output"] / 1_000_000
        + usage.cache_read_input_tokens * pricing["cache_read"] / 1_000_000
        + usage.cache_creation_input_tokens * pricing["cache_creation"] / 1_000_000
    )
    return cost
