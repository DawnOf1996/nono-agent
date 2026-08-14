"""Prompt 过长恢复策略。

提供 prompt_too_long 错误的具体恢复实现：
- 反应式压缩（截断工具结果、剥离思考块）
- 上下文崩溃排空（刷新已暂存的崩溃）
"""

from __future__ import annotations

import logging

from nono_agent.core.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)
from nono_agent.utils.tokens import count_tokens

logger = logging.getLogger(__name__)


async def reactive_compact(
    messages: list[Message],
    max_tool_result_tokens: int = 4_000,
) -> tuple[list[Message], int]:
    """应用反应式压缩以减少 token 数。

    步骤：
    1. 剥离所有 assistant 消息中的思考块
    2. 截断 tool_result 内容块
    3. 返回压缩后的消息和节省的 token 数
    """
    saved = 0
    result: list[Message] = []

    for msg in messages:
        if isinstance(msg, AssistantMessage):
            new_msg, s = _strip_thinking_blocks(msg)
            saved += s
            result.append(new_msg)
        elif isinstance(msg, UserMessage):
            new_msg, s = _truncate_tool_results(msg, max_tool_result_tokens)
            saved += s
            result.append(new_msg)
        else:
            result.append(msg)

    logger.info("Reactive compact: saved ~%d tokens", saved)
    return result, saved


def _strip_thinking_blocks(msg: AssistantMessage) -> tuple[AssistantMessage, int]:
    """从 assistant 消息中移除思考块 —— 节省 token 但保留文本和工具调用。"""
    content = msg.message.get("content", [])
    if not isinstance(content, list):
        return msg, 0

    saved = 0
    new_content = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"):
            text = block.get("thinking", "") or block.get("data", "")
            saved += count_tokens(text)
        else:
            new_content.append(block)

    if saved == 0:
        return msg, 0

    new_message = msg.model_copy(deep=True)
    new_message.message["content"] = new_content
    return new_message, saved


def _truncate_tool_results(
    msg: UserMessage,
    max_tokens: int,
) -> tuple[UserMessage, int]:
    """截断过大的 tool_result 内容块 —— 保留头尾，中间省略。"""
    content = msg.message.get("content", [])
    if not isinstance(content, list):
        return msg, 0

    saved = 0
    new_content = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            raw = block.get("content", "")
            if isinstance(raw, str):
                tokens = count_tokens(raw)
                if tokens > max_tokens:
                    # 保留头部 + 尾部，中间用截断标记替换
                    head_chars = max_tokens * 3
                    tail_chars = max_tokens
                    truncated = (
                        raw[:head_chars]
                        + f"\n... [{tokens - max_tokens} tokens truncated] ...\n"
                        + raw[-tail_chars:]
                    )
                    new_content.append({**block, "content": truncated})
                    saved += tokens - max_tokens
                    continue
        new_content.append(block)

    if saved == 0:
        return msg, 0

    new_msg = msg.model_copy(deep=True)
    new_msg.message["content"] = new_content
    return new_msg, saved


async def context_collapse_drain(
    messages: list[Message],
    stages_to_drain: int = 2,
) -> tuple[list[Message], int]:
    """排空已暂存的上下文崩溃以释放 token。

    移除最旧的 N 对 user/assistant 消息并用摘要标记替换。
    这是有损压缩 —— 信息会丢失。

    Returns:
        修改后的消息列表和节省的 token 数
    """
    if len(messages) <= 4:
        return messages, 0

    # 查找要排空的消息对（跳过开头的系统消息）
    start = 0
    if isinstance(messages[0], SystemMessage):
        start = 1

    pairs_to_drain = min(stages_to_drain, (len(messages) - start - 2) // 2)
    if pairs_to_drain <= 0:
        return messages, 0

    # 计算待排空消息的 token 数
    drain_end = start + pairs_to_drain * 2
    saved = 0
    for msg in messages[start:drain_end]:
        if hasattr(msg, "message"):
            content = msg.message.get("content", "")
            if isinstance(content, str):
                saved += count_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        saved += count_tokens(
                            block.get("text", "") or str(block.get("input", ""))
                        )

    # 创建排空标记 —— 记录压缩元数据
    marker = SystemMessage(
        subtype="compact_boundary",
        content=f"[{pairs_to_drain * 2} earlier messages were collapsed to free context space]",
        compact_metadata={
            "strategy": "collapse_drain",
            "messages_drained": pairs_to_drain * 2,
            "estimated_tokens_saved": saved,
        },
    )

    result = messages[:start] + [marker] + messages[drain_end:]
    logger.info("Context collapse drain: removed %d messages, saved ~%d tokens",
                pairs_to_drain * 2, saved)

    return result, saved
