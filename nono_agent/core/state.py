"""Agentic Loop 状态机类型定义。

定义了循环“为什么继续”(TransitionReason, 7种) 和“为什么结束”(TerminalReason, 10种)，
以及 LoopState —— 不可变的跨迭代状态，每次 continue 时构造新实例。

设计亮点：
- frozen=True dataclass 实现不可变性，防止漏写字段（TS 版常见 bug）
- Enum + match/case 穷举所有转移/终止原因，编译器保证完备性
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class TransitionReason(Enum):
    """循环继续的原因 —— 为什么上一轮迭代后还要继续。

    7 种转移原因覆盖了所有需要继续循环的场景：
    """

    # 正常下一轮: LLM 生成了 tool_use 且工具执行完成
    NEXT_TURN = auto()

    # max_output_tokens 达到上限: 从默认的 8k 升级到 64k
    MAX_OUTPUT_TOKENS_ESCALATE = auto()

    # max_output_tokens 达到上限: 注入恢复提示让模型继续
    MAX_OUTPUT_TOKENS_RECOVERY = auto()

    # prompt 太长: 反应式压缩成功，用压缩后的上下文重试
    REACTIVE_COMPACT_RETRY = auto()

    # prompt 太长: 上下文折叠排水成功，重试
    COLLAPSE_DRAIN_RETRY = auto()

    # stop hook 返回阻塞错误: 注入错误信息让模型重试
    STOP_HOOK_BLOCKING = auto()

    # token 预算继续: 注入提示消息让模型继续工作
    TOKEN_BUDGET_CONTINUATION = auto()


class TerminalReason(Enum):
    """循环终止的原因 —— 10 种可能的终止场景。"""

    COMPLETED = auto()             # 正常完成 (stop_reason="end_turn")
    ABORTED_STREAMING = auto()     # 流式传输期间被用户取消
    ABORTED_TOOLS = auto()         # 工具执行期间被用户取消
    PROMPT_TOO_LONG = auto()       # prompt 超长且恢复策略耗尽
    IMAGE_ERROR = auto()           # 图片/媒体文件过大
    MODEL_ERROR = auto()           # 模型 API 错误（过载、速率限制等）
    BLOCKING_LIMIT = auto()        # token 用量达到阻塞上限 (95% 上下文窗口)
    STOP_HOOK_PREVENTED = auto()   # stop hook 明确阻止继续
    HOOK_STOPPED = auto()          # middleware 在 on_transition 中否决继续
    MAX_TURNS = auto()             # 达到最大轮次限制
    MAX_BUDGET_USD = auto()        # 超出美元预算 (max_budget_usd)
    TASK_BUDGET_EXHAUSTED = auto() # 超出 token 预算 (task_budget)


@dataclass(frozen=True)
class Transition:
    """描述循环为什么继续 —— 包含原因和可选的元数据。"""

    reason: TransitionReason                          # 转移原因
    metadata: dict[str, Any] = field(default_factory=dict)  # 附加信息（如 tokens_saved）


@dataclass(frozen=True)
class Terminal:
    """描述循环为什么结束 —— 包含原因、可选的错误和轮次数。"""

    reason: TerminalReason            # 终止原因
    error: Exception | None = None    # 导致终止的异常
    turn_count: int | None = None     # 终止时的轮次计数


@dataclass(frozen=True)
class AutoCompactTracking:
    """跟踪自动压缩状态 —— 跨迭代维护，避免压缩死循环。"""

    compacted: bool = False                # 是否已执行过压缩
    turn_id: str = ""                      # 当前轮次 ID
    turn_counter: int = 0                  # 当前轮次计数
    consecutive_failures: int = 0          # 连续压缩失败次数（防止死循环）


@dataclass(frozen=True)
class LoopState:
    """不可变的循环迭代状态 —— agentic loop 的核心状态容器。

    关键设计：
    - frozen=True 保证不可变性，每次状态转移必须构造新实例
    - 这解决了 TS 版的常见 bug: 漏写某个字段的更新
    - 每个字段都有明确的转移规则（在 loop.py 的 _apply_recovery 和主循环中）
    """

    # 消息存储（引用可变，但 compact 时会替换整个引用）
    messages: Any  # MessageStore — 前向引用避免循环导入

    # 工具执行上下文（在一轮迭代内可能被重新赋值）
    tool_use_context: Any  # ToolUseContext

    # 自动压缩跟踪状态（跨迭代维护）
    auto_compact_tracking: AutoCompactTracking | None = None

    # max_output_tokens 恢复计数器（正常 next_turn 时重置为 0）
    max_output_tokens_recovery_count: int = 0

    # 反应式压缩防护标志（防止 prompt_too_long -> compact -> prompt_too_long 死循环）
    has_attempted_reactive_compact: bool = False

    # max_output_tokens 覆盖值（升级时设置为 64k，完成后清除）
    max_output_tokens_override: int | None = None

    # 上一轮的待处理工具摘要任务（当前轮中解析）
    pending_tool_use_summary: Any | None = None  # asyncio.Task | None

    # stop hook 是否当前活跃
    stop_hook_active: bool | None = None

    # 当前轮次计数（每次 next_turn 递增）
    turn_count: int = 1

    # 上一轮的转移原因（第一轮为 None）
    transition: Transition | None = None
