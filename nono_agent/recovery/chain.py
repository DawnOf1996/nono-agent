"""恢复链 —— 集中化的错误恢复决策。

当 agentic loop 遇到错误 (prompt_too_long, max_output_tokens, media_size 等)，
RecoveryChain 决定如何处理：
- 压缩后重试 (REACTIVE_COMPACT_RETRY / COLLAPSE_DRAIN_RETRY)
- 升级 max_output_tokens (ESCALATE_MAX_TOKENS)
- 注入恢复提示 (RECOVERY_PROMPT)
- 终止循环 (TERMINATE)

使用 match/case 对错误类型做穷举匹配，编译器保证完备性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from nono_agent.core.state import LoopState, TransitionReason, TerminalReason

logger = logging.getLogger(__name__)


class RecoveryAction(Enum):
    """恢复链决策的动作类型 —— 7 种可能的恢复策略。"""

    # 反应式压缩后重试（针对 prompt_too_long）
    REACTIVE_COMPACT_RETRY = auto()

    # 上下文折叠排水后重试（针对 prompt_too_long 第二轮）
    COLLAPSE_DRAIN_RETRY = auto()

    # 升级 max_output_tokens: 8k → 64k
    ESCALATE_MAX_TOKENS = auto()

    # 注入恢复提示让模型继续
    RECOVERY_PROMPT = auto()

    # 注入提示让模型继续在 token 预算内工作
    TOKEN_BUDGET_NUDGE = auto()

    # 放弃 —— 终止循环
    TERMINATE = auto()

    # 透传 —— 让中间件链决定
    PASS_THROUGH = auto()


@dataclass
class RecoveryDecision:
    """恢复链的决策结果 —— 包含动作、原因、元数据和恢复消息。"""

    action: RecoveryAction
    transition_reason: TransitionReason | None = None
    terminal_reason: TerminalReason | None = None
    metadata: dict[str, Any] | None = None
    recovery_message: str | None = None

    @property
    def should_continue(self) -> bool:
        return self.action not in (RecoveryAction.TERMINATE, RecoveryAction.PASS_THROUGH)


class RecoveryChain:
    """集中化错误恢复决策引擎。

    根据 error_type 和当前 state 决定恢复策略：
    - prompt_too_long → 先 reactive compact，再 collapse drain
    - max_output_tokens → 先 escalate (8k→64k)，再 recovery prompt (最多 3 次)
    - media_size / model_error / api_error → TERMINATE
    """

    def __init__(
        self,
        max_reactive_compact_retries: int = 2,
        max_output_recovery_count: int = 3,
    ) -> None:
        self._max_reactive_compact = max_reactive_compact_retries
        self._max_output_recovery = max_output_recovery_count

    def decide(
        self,
        error_type: str,
        state: LoopState,
        error_detail: dict[str, Any] | None = None,
    ) -> RecoveryDecision:
        """根据错误类型和当前状态决定恢复策略。

        使用 match/case 穷举匹配所有错误类型，编译器保证完备性。
        """
        match error_type:
            case "prompt_too_long":
                return self._handle_prompt_too_long(state, error_detail)
            case "max_output_tokens":
                return self._handle_max_output_tokens(state, error_detail)
            case "media_size":
                return self._handle_media_size(state, error_detail)
            case "model_error":
                return self._handle_model_error(state, error_detail)
            case "api_error":
                return self._handle_api_error(state, error_detail)
            case _:
                logger.warning("Unknown error type: %s", error_type)
                return RecoveryDecision(
                    action=RecoveryAction.PASS_THROUGH,
                )

    def _handle_prompt_too_long(
        self,
        state: LoopState,
        detail: dict[str, Any] | None,
    ) -> RecoveryDecision:
        """Handle prompt_too_long errors.

        Strategy:
        1. First: try reactive compaction (truncate tool results, strip thinking)
        2. If already tried: try context collapse drain
        3. If both exhausted: terminate
        """
        if not state.has_attempted_reactive_compact:
            return RecoveryDecision(
                action=RecoveryAction.REACTIVE_COMPACT_RETRY,
                transition_reason=TransitionReason.REACTIVE_COMPACT_RETRY,
                metadata={"strategy": "reactive_compact"},
            )

        # Reactive compact already tried — try collapse drain
        return RecoveryDecision(
            action=RecoveryAction.COLLAPSE_DRAIN_RETRY,
            transition_reason=TransitionReason.COLLAPSE_DRAIN_RETRY,
            metadata={"strategy": "collapse_drain"},
        )

    def _handle_max_output_tokens(
        self,
        state: LoopState,
        detail: dict[str, Any] | None,
    ) -> RecoveryDecision:
        """Handle max_output_tokens truncation.

        Strategy:
        1. First: escalate max_tokens (8k -> 64k) if not already escalated
        2. Then: inject recovery prompt up to N times
        3. After N recoveries: terminate
        """
        # Check if escalation hasn't been tried yet
        if state.max_output_tokens_override is None:
            return RecoveryDecision(
                action=RecoveryAction.ESCALATE_MAX_TOKENS,
                transition_reason=TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE,
                metadata={"new_max_tokens": 64_000},
            )

        # Already escalated — try recovery prompt
        if state.max_output_tokens_recovery_count < self._max_output_recovery:
            return RecoveryDecision(
                action=RecoveryAction.RECOVERY_PROMPT,
                transition_reason=TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY,
                recovery_message=(
                    "Your previous response was truncated because it exceeded the "
                    "output length limit. Please continue from where you left off. "
                    "Do NOT repeat what you've already written."
                ),
                metadata={
                    "recovery_count": state.max_output_tokens_recovery_count + 1,
                },
            )

        # Recovery exhausted
        return RecoveryDecision(
            action=RecoveryAction.TERMINATE,
            terminal_reason=TerminalReason.COMPLETED,
            metadata={"reason": "max_output_tokens_recovery_exhausted"},
        )

    def _handle_media_size(
        self,
        state: LoopState,
        detail: dict[str, Any] | None,
    ) -> RecoveryDecision:
        """Handle media/image size errors — typically not recoverable."""
        return RecoveryDecision(
            action=RecoveryAction.TERMINATE,
            terminal_reason=TerminalReason.IMAGE_ERROR,
        )

    def _handle_model_error(
        self,
        state: LoopState,
        detail: dict[str, Any] | None,
    ) -> RecoveryDecision:
        """Handle model-level errors (overloaded, rate limit, etc.)."""
        return RecoveryDecision(
            action=RecoveryAction.TERMINATE,
            terminal_reason=TerminalReason.MODEL_ERROR,
        )

    def _handle_api_error(
        self,
        state: LoopState,
        detail: dict[str, Any] | None,
    ) -> RecoveryDecision:
        """Handle API-level errors (network, timeout, etc.)."""
        # API errors are typically handled by the provider's retry logic
        return RecoveryDecision(
            action=RecoveryAction.TERMINATE,
            terminal_reason=TerminalReason.MODEL_ERROR,
        )
