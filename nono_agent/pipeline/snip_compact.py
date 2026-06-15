"""滑动窗口压缩阶段 —— 零成本丢弃旧消息。

不同于完整压缩（使用 LLM 摘要），滑动窗口压缩只是简单地丢弃最旧的消息。
这是最便宜的压缩策略，优先执行。
"""

from __future__ import annotations

import logging

from nono_agent.core.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)
from nono_agent.pipeline.base import PipelineContext
from nono_agent.utils.tokens import (
    estimate_message_tokens,
    get_context_window_size,
)

logger = logging.getLogger(__name__)

# 当 token 超过上下文窗口的此比例时触发 snip
SNIP_THRESHOLD = 0.60

# 始终保留至少这么多对最近消息
MIN_PRESERVE_PAIRS = 3


class SnipCompactStage:
    """滑动窗口压缩 —— token 超限时丢弃最旧消息。

    Priority: 20（在 tool_result_budget 之后，重型 stage 之前）。
    """

    def __init__(
        self,
        threshold: float = SNIP_THRESHOLD,
        min_preserve_pairs: int = MIN_PRESERVE_PAIRS,
    ) -> None:
        self._threshold = threshold
        self._min_preserve = min_preserve_pairs

    @property
    def name(self) -> str:
        return "snip_compact"

    @property
    def priority(self) -> int:
        return 20

    def should_run(self, messages, context) -> bool:
        # 仅在 token 超过 snip 阈值时运行
        if not context.model_id:
            return False
        window = get_context_window_size(context.model_id)
        limit = int(window * self._threshold)
        return context.estimated_tokens > limit

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> tuple[list[Message], PipelineContext]:
        window = get_context_window_size(context.model_id)
        target = int(window * self._threshold * 0.8)  # Shrink to 80% of threshold

        # Calculate per-message tokens
        msg_tokens: list[int] = []
        for msg in messages:
            if hasattr(msg, "message"):
                msg_tokens.append(estimate_message_tokens(msg.message))
            elif hasattr(msg, "content"):
                msg_tokens.append(
                    len(getattr(msg, "content", "")) // 4  # rough estimate
                )
            else:
                msg_tokens.append(10)

        total = sum(msg_tokens)

        # Find how many messages to drop from the front
        # Always preserve system messages at the start and recent messages
        min_preserve_msgs = self._min_preserve * 2  # user + assistant pairs
        drop_count = 0
        for i in range(len(messages) - min_preserve_msgs):
            msg = messages[i]
            # Never drop system messages or compact boundaries
            if isinstance(msg, SystemMessage):
                continue
            if total <= target:
                break
            total -= msg_tokens[i]
            drop_count = i + 1

        if drop_count > 0:
            # Insert a system message noting the snip
            snip_note = SystemMessage(
                subtype="api_retry",
                content=f"[{drop_count} earlier messages were removed to fit context window]",
            )
            remaining = [snip_note] + messages[drop_count:]
            logger.info("Snip compact: dropped %d messages, saved ~%d tokens",
                        drop_count, sum(msg_tokens[:drop_count]))
            context.estimated_tokens = total
            return remaining, context

        return messages, context
