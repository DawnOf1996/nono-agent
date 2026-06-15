"""上下文折叠阶段 —— 使用 LLM 摘要压缩旧消息。

当更便宜的策略（snip/microcompact）不够时，本 stage 使用 LLM 调用
将旧的对话轮次压缩为紧凑的摘要消息。

成本较高（消耗 API token）但能更好地保留语义上下文。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from nono_agent.core.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)
from nono_agent.pipeline.base import PipelineContext
from nono_agent.utils.tokens import count_tokens, get_context_window_size

logger = logging.getLogger(__name__)

# 上下文折叠在此上下文窗口比例时触发
COLLAPSE_THRESHOLD = 0.75

# 目标: 将旧消息压缩到此比例的原始 token 数
COLLAPSE_TARGET_RATIO = 0.3

# 摘要函数类型别名
Summarizer = Callable[[list[dict[str, Any]], str], Awaitable[str]]


class ContextCollapseStage:
    """LLM 摘要上下文折叠 —— 适用于深度对话。

    Priority: 150（较重操作，在 microcompact 之后执行）。

    需要注入一个 summarizer 函数，通常是轻量级 LLM 调用（如用 Haiku 摘要 Sonnet 对话）。
    """

    def __init__(
        self,
        summarizer: Summarizer | None = None,
        threshold: float = COLLAPSE_THRESHOLD,
    ) -> None:
        self._summarizer = summarizer
        self._threshold = threshold

    @property
    def name(self) -> str:
        return "context_collapse"

    @property
    def priority(self) -> int:
        return 150

    def should_run(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> bool:
        if not context.model_id or self._summarizer is None:
            return False
        window = get_context_window_size(context.model_id)
        return context.estimated_tokens > int(window * self._threshold)

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> tuple[list[Message], PipelineContext]:
        assert self._summarizer is not None

        # Split messages into "old" (to summarize) and "recent" (to keep)
        split_point = self._find_split_point(messages)
        if split_point <= 0:
            return messages, context

        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]

        # Convert old messages to simple dict format for the summarizer
        old_dicts = []
        for msg in old_messages:
            if isinstance(msg, (UserMessage, AssistantMessage)):
                old_dicts.append(msg.message)

        if not old_dicts:
            return messages, context

        # Call the summarizer
        try:
            summary = await self._summarizer(old_dicts, context.model_id)
        except Exception:
            logger.exception("Context collapse summarizer failed")
            return messages, context

        # Create compact boundary + summary message
        boundary = SystemMessage(
            subtype="compact_boundary",
            content="[Context was summarized to fit within the context window]",
            compact_metadata={
                "original_message_count": len(old_messages),
                "summary_token_estimate": count_tokens(summary),
            },
        )

        summary_msg = UserMessage(
            message={
                "role": "user",
                "content": f"[Previous conversation summary]\n{summary}",
            },
            is_compact_summary=True,
        )

        result = [boundary, summary_msg] + recent_messages

        # Update token estimate
        old_tokens = sum(
            count_tokens(str(getattr(m, "message", ""))) for m in old_messages
        )
        new_tokens = count_tokens(summary)
        saved = old_tokens - new_tokens
        context.estimated_tokens = max(0, context.estimated_tokens - saved)
        context.metadata["compact_boundary_created"] = True

        logger.info(
            "Context collapse: summarized %d messages, saved ~%d tokens",
            len(old_messages),
            saved,
        )

        return result, context

    def _find_split_point(self, messages: list[Message]) -> int:
        """找到旧消息和最近消息的分界点 —— 保留至少 3 对最近消息。"""
        min_recent = 6  # Keep at least 3 pairs
        if len(messages) <= min_recent:
            return 0

        # Find the last user message before the recent window
        split = len(messages) - min_recent
        for i in range(split, -1, -1):
            if isinstance(messages[i], UserMessage):
                return i

        return split
