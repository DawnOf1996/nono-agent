"""最大输出 token 恢复策略。

当 LLM 响应因 max_output_tokens 截断时：
1. 升级（Escalate）：将 max_tokens 从默认值提升到更高值
2. 恢复提示（Recovery prompt）：注入消息要求模型继续
"""

from __future__ import annotations

import logging
from typing import Any

from nono_agent.core.types import UserMessage

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 8_192      # 默认最大输出 token 数
ESCALATED_MAX_TOKENS = 64_000   # 升级后的最大输出 token 数

# 恢复提示模板 —— 当 max_output_tokens 截断发生时注入
RECOVERY_PROMPT_TEMPLATE = (
    "Your previous response was truncated because it exceeded the output length limit. "
    "Please continue from where you left off. "
    "Do NOT repeat what you've already written. "
    "Do NOT start with apologies or acknowledgments. "
    "Just continue the task directly."
)


def should_escalate(
    current_max_tokens: int | None,
) -> bool:
    """是否应升级 max_output_tokens —— 当前值低于升级阈值时返回 True。"""
    return current_max_tokens is None or current_max_tokens < ESCALATED_MAX_TOKENS


def get_escalated_value() -> int:
    """获取升级后的 max_output_tokens 值。"""
    return ESCALATED_MAX_TOKENS


def create_recovery_message(
    recovery_count: int,
    custom_prompt: str | None = None,
) -> UserMessage:
    """创建用于 max_output_tokens 恢复的注入消息。

    Args:
        recovery_count: 已尝试恢复的次数
        custom_prompt: 可选的自定义恢复提示

    Returns:
        待注入到对话中的 UserMessage
    """
    prompt = custom_prompt or RECOVERY_PROMPT_TEMPLATE

    if recovery_count > 0:
        prompt += (
            f"\n\n(Note: this is recovery attempt #{recovery_count + 1}. "
            "Please be more concise if possible.)"
        )

    return UserMessage(
        message={"role": "user", "content": prompt},
        is_meta=True,
    )
