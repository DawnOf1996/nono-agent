"""自动压缩阶段 —— 安全网，防止 prompt_too_long 错误。

监控 token 用量，当接近上下文窗口限制时自动触发压缩。
两级策略：
- 80% 阈值: 激进截断（剥离 thinking + 截断 tool results）
- 95% 阻塞上限: 激进压缩（仅保留系统消息 + 最近 2 轮）
"""

from __future__ import annotations

import logging

from nono_agent.core.types import AssistantMessage, Message, SystemMessage, UserMessage
from nono_agent.pipeline.base import PipelineContext
from nono_agent.utils.tokens import (
    AUTO_COMPACT_THRESHOLD,
    BLOCKING_LIMIT_THRESHOLD,
    COMPACT_TARGET_RATIO,
    estimate_messages_tokens,
)

logger = logging.getLogger(__name__)


class AutoCompactStage:
    """安全网 —— 接近上下文窗口限制时强制压缩。

    Priority: 200（最后执行，最重的变换）。

    两级策略：
    1. 80% 阈值: 激进截断（剥离 thinking + 截断 tool results）
    2. 95% 阻塞上限: 激进压缩（仅保留系统消息 + 最近 2 轮）
    """

    @property
    def name(self) -> str:
        return "auto_compact"

    @property
    def priority(self) -> int:
        return 200

    def should_run(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> bool:
        auto_limit = int(context.context_window * AUTO_COMPACT_THRESHOLD)
        return context.effective_tokens >= auto_limit

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> tuple[list[Message], PipelineContext]:
        window = context.context_window
        blocking_limit = int(window * BLOCKING_LIMIT_THRESHOLD)

        # 是否超过阻塞上限（95%）
        # 判定走 effective_tokens —— 95% 是最后一道防线，宁可早压不可撞限。
        is_blocking = context.effective_tokens >= blocking_limit

        if is_blocking:
            # CRITICAL: 超过阻塞上限 —— 激进压缩（仅保留最近 2 轮）
            logger.warning(
                "Auto compact: CRITICAL — %d tokens (estimate %d) exceeds blocking limit %d",
                context.effective_tokens,
                context.estimated_tokens,
                blocking_limit,
            )
            # Aggressive: keep only system prompt + last 2 turns
            messages = self._aggressive_compact(messages)
            context.estimated_tokens = self._reestimate(messages, context)
            context.metadata["compact_boundary_created"] = True
        else:
            # 超过 80% 阈值但未到阻塞上限 —— 激进截断（剥离 thinking + 截断 tool results）
            logger.info(
                "Auto compact: %d tokens (estimate %d) above threshold, "
                "applying aggressive truncation",
                context.effective_tokens,
                context.estimated_tokens,
            )
            # Moderate: truncate all tool results and long texts
            messages = self._moderate_compact(messages, context)

        return messages, context

    def _aggressive_compact(self, messages: list[Message]) -> list[Message]:
        """激进压缩 —— 仅保留系统消息 + 最近 2 对用户/助手消息。"""
        # Preserve system messages
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]

        # Find last 2 user messages
        user_indices = [
            i for i, m in enumerate(messages) if isinstance(m, UserMessage)
        ]
        if len(user_indices) <= 2:
            return messages  # Nothing to compact

        cutoff = user_indices[-2]
        recent = messages[cutoff:]

        # Insert boundary marker
        boundary = SystemMessage(
            subtype="compact_boundary",
            content=(
                "[Auto-compact: Earlier conversation was removed to fit context window. "
                "Only the most recent messages are preserved.]"
            ),
            compact_metadata={
                "original_message_count": len(messages),
                "preserved_from_index": cutoff,
                "strategy": "aggressive",
            },
        )

        return system_msgs + [boundary] + recent

    def _moderate_compact(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> list[Message]:
        """激进截断 —— 剥离所有 thinking 块 + 截断所有 tool results。"""
        window = context.context_window
        target = int(window * COMPACT_TARGET_RATIO)  # Try to get down to target

        for msg in messages:
            if context.estimated_tokens <= target:
                break

            if isinstance(msg, AssistantMessage):
                self._strip_thinking(msg)

            if isinstance(msg, UserMessage):
                self._truncate_tool_results(msg, max_tokens=2_000)

        # Re-estimate（含系统提示词/工具的固定开销）
        context.estimated_tokens = self._reestimate(messages, context)

        return messages

    @staticmethod
    def _reestimate(messages: list[Message], context: PipelineContext) -> int:
        """重新估算 token 总量 —— 消息部分 + 系统提示词/工具的固定开销。"""
        return estimate_messages_tokens(
            [m.message for m in messages if hasattr(m, "message")]
        ) + context.overhead_tokens

    @staticmethod
    def _strip_thinking(msg: Message) -> None:
        """从助手消息中移除所有 thinking 块。"""
        if not isinstance(msg, AssistantMessage):
            return
        content = msg.message.get("content", [])
        if not isinstance(content, list):
            return
        msg.message["content"] = [
            b for b in content
            if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
        ]

    @staticmethod
    def _truncate_tool_results(msg: Message, max_tokens: int = 2_000) -> None:
        """截断工具结果内容块 —— 保留头部和尾部。"""
        if not isinstance(msg, UserMessage):
            return
        content = msg.message.get("content", [])
        if not isinstance(content, list):
            return

        from nono_agent.utils.tokens import count_tokens

        for i, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            raw = block.get("content", "")
            if isinstance(raw, str) and count_tokens(raw) > max_tokens:
                head = raw[: max_tokens * 3]
                tail = raw[-max_tokens:]
                content[i] = {
                    **block,
                    "content": head + "\n... [truncated] ...\n" + tail,
                }
