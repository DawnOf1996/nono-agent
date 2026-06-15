"""工具结果预算阶段 —— 截断超长的工具返回内容。

当工具返回超大内容（如读取大文件）时，本 stage 将其截断到 token 预算内，
保留开头和结尾的内容，中间插入摘要标记。
"""

from __future__ import annotations

import logging
from typing import Any

from nono_agent.core.types import Message, UserMessage
from nono_agent.pipeline.base import PipelineContext
from nono_agent.utils.tokens import count_tokens

logger = logging.getLogger(__name__)

# 每个工具结果的默认最大 token 数
DEFAULT_TOOL_RESULT_BUDGET = 20_000

# 始终保留的头部 token 数
PRESERVE_HEAD_TOKENS = 500

# 始终保留的尾部 token 数
PRESERVE_TAIL_TOKENS = 200


class ToolResultBudgetStage:
    """截断超长的 tool_result 内容块。

    Priority: 10（最早执行，轻量变换）。
    """

    def __init__(
        self,
        budget_per_result: int = DEFAULT_TOOL_RESULT_BUDGET,
    ) -> None:
        self._budget = budget_per_result

    @property
    def name(self) -> str:
        return "tool_result_budget"

    @property
    def priority(self) -> int:
        return 10

    def should_run(self, messages, context) -> bool:
        # 始终运行 —— 检查代价低，大多数对话都有工具结果
        return True

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> tuple[list[Message], PipelineContext]:
        modified = False

        for msg in messages:
            if not isinstance(msg, UserMessage):
                continue

            content = msg.message.get("content", [])
            if not isinstance(content, list):
                continue

            for i, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue

                raw_content = block.get("content", "")
                if isinstance(raw_content, str):
                    tokens = count_tokens(raw_content)
                    if tokens > self._budget:
                        truncated = _truncate_text(
                            raw_content,
                            self._budget,
                            PRESERVE_HEAD_TOKENS,
                            PRESERVE_TAIL_TOKENS,
                        )
                        content[i] = {**block, "content": truncated}
                        modified = True
                        logger.debug(
                            "Truncated tool_result %s: %d -> ~%d tokens",
                            block.get("tool_use_id", "?"),
                            tokens,
                            self._budget,
                        )

        return messages, context


def _truncate_text(
    text: str,
    budget: int,
    head_tokens: int,
    tail_tokens: int,
) -> str:
    """截断文本 —— 保留头部和尾部，中间插入省略摘要。"""
    lines = text.split("\n")
    total_chars = len(text)

    # Estimate lines to keep
    chars_per_token = max(1, total_chars // max(1, count_tokens(text)))
    head_chars = head_tokens * chars_per_token
    tail_chars = tail_tokens * chars_per_token

    # Accumulate head lines
    head_lines: list[str] = []
    head_size = 0
    for line in lines:
        if head_size + len(line) > head_chars:
            break
        head_lines.append(line)
        head_size += len(line) + 1

    # Accumulate tail lines (from end)
    tail_lines: list[str] = []
    tail_size = 0
    for line in reversed(lines):
        if tail_size + len(line) > tail_chars:
            break
        tail_lines.insert(0, line)
        tail_size += len(line) + 1

    skipped = len(lines) - len(head_lines) - len(tail_lines)
    summary = f"\n... [{skipped} lines omitted, truncated to fit token budget] ...\n"

    result = "\n".join(head_lines) + summary + "\n".join(tail_lines)
    return result
