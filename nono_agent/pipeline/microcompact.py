"""微压缩阶段 —— 细粒度内容压缩。

不使用 LLM，仅通过字符级操作缩小单条消息内容：
- 移除旧助手消息中的 thinking 块
- 截断长文本块
- 剥离冗余的工具结果细节
"""

from __future__ import annotations

import logging

from nono_agent.core.types import AssistantMessage, Message, UserMessage
from nono_agent.pipeline.base import PipelineContext
from nono_agent.utils.tokens import (
    MICROCOMPACT_THRESHOLD,
    count_tokens,
)

logger = logging.getLogger(__name__)

# 仅微压缩距末尾超过此轮数的消息
MICROCOMPACT_TURN_LOOKBACK = 4

# 旧消息中每个文本块的最大 token 数
MAX_TEXT_BLOCK_TOKENS = 4_000


class MicroCompactStage:
    """旧消息细粒度压缩 —— 剥离 thinking + 截断长文本。

    Priority: 110（中等成本，在 snip_compact 之后执行）。
    """

    def __init__(
        self,
        turn_lookback: int = MICROCOMPACT_TURN_LOOKBACK,
        max_text_tokens: int = MAX_TEXT_BLOCK_TOKENS,
    ) -> None:
        self._lookback = turn_lookback
        self._max_tokens = max_text_tokens

    @property
    def name(self) -> str:
        return "microcompact"

    @property
    def priority(self) -> int:
        return 110

    def should_run(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> bool:
        # 仅在上下文使用率超过阈值时触发（阈值基于配置的上下文窗口）
        return context.estimated_tokens > int(context.context_window * MICROCOMPACT_THRESHOLD)

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> tuple[list[Message], PipelineContext]:
        if len(messages) <= self._lookback * 2:
            return messages, context

        # Only compact messages older than the lookback window
        boundary = len(messages) - self._lookback * 2
        saved = 0

        for i in range(boundary):
            msg = messages[i]

            if isinstance(msg, AssistantMessage):
                saved += self._compact_assistant(msg)
            elif isinstance(msg, UserMessage):
                saved += self._compact_user(msg)

        if saved > 0:
            context.estimated_tokens = max(0, context.estimated_tokens - saved)
            logger.info("Microcompact: saved ~%d tokens from %d older messages",
                        saved, boundary)

        return messages, context

    def _compact_assistant(self, msg: AssistantMessage) -> int:
        """压缩旧助手消息 —— 移除 thinking 块 + 截断长文本。"""
        saved = 0
        content = msg.message.get("content", [])
        if not isinstance(content, list):
            return 0

        new_content = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type", "")

            # Remove thinking blocks entirely from old messages
            if block_type in ("thinking", "redacted_thinking"):
                saved += count_tokens(
                    block.get("thinking", "") or block.get("data", "")
                )
                continue

            # Truncate long text blocks
            if block_type == "text":
                text = block.get("text", "")
                tokens = count_tokens(text)
                if tokens > self._max_tokens:
                    truncated = _truncate_chars(text, self._max_tokens * 4)
                    new_content.append({**block, "text": truncated})
                    saved += tokens - count_tokens(truncated)
                    continue

            new_content.append(block)

        msg.message["content"] = new_content
        return saved

    def _compact_user(self, msg: UserMessage) -> int:
        """压缩旧用户消息中的 tool_result 块。"""
        saved = 0
        content = msg.message.get("content", [])
        if not isinstance(content, list):
            return 0

        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue

            raw = block.get("content", "")
            if isinstance(raw, str):
                tokens = count_tokens(raw)
                if tokens > self._max_tokens:
                    truncated = _truncate_chars(raw, self._max_tokens * 4)
                    content[i] = {**block, "content": truncated}
                    saved += tokens - count_tokens(truncated)

        return saved


def _truncate_chars(text: str, max_chars: int) -> str:
    """截断文本到 max_chars，保留头部和尾部。"""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars // 3
    return text[:head] + f"\n... [{len(text) - head - tail} chars truncated] ...\n" + text[-tail:]
