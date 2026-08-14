"""Token 计数与估算工具。

提供基于 tiktoken 的精确 token 计数和回退估算，
以及上下文窗口管理的辅助函数。
"""

from __future__ import annotations

import json
from typing import Any

# 懒加载 tiktoken 以避免硬依赖
_tiktoken_encoder: Any = None


def _get_encoder() -> Any:
    """懒加载 tiktoken 编码器 —— 首次调用时初始化。"""
    global _tiktoken_encoder
    if _tiktoken_encoder is None:
        try:
            import tiktoken  # type: ignore[import-not-found]

            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            return None
    return _tiktoken_encoder


def count_tokens(text: str) -> int:
    """计算文本的 token 数。

    优先使用 tiktoken 精确计数，不可用时回退到 ~4字符/token 的粗略估算。
    """
    encoder = _get_encoder()
    if encoder is not None:
        return len(encoder.encode(text))
    # 回退估算：~4 字符/token
    return max(1, len(text) // 4)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """估算单条消息的 token 数。

    包含 role、content blocks 和消息开销。
    支持文本、工具调用输入和工具结果等各种内容块类型。
    """
    total = 4  # 消息开销（role + 分隔符等）
    role = message.get("role", "")
    total += count_tokens(role)

    content = message.get("content", "")
    if isinstance(content, str):
        total += count_tokens(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if text:
                    total += count_tokens(text)
                # 工具调用输入参数
                inp = block.get("input", "")
                if inp:
                    total += count_tokens(json.dumps(inp, ensure_ascii=False))
                # 工具结果内容
                result_content = block.get("content", "")
                if isinstance(result_content, str) and result_content:
                    total += count_tokens(result_content)
            elif isinstance(block, str):
                total += count_tokens(block)

    return total


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算消息列表的总 token 数。"""
    return sum(estimate_message_tokens(m) for m in messages)


# ---------------------------------------------------------------------------
# 上下文窗口辅助 —— 模型上下文大小和自动压缩阈值
# ---------------------------------------------------------------------------

# 各模型家族的上下文窗口大小（token 数）
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "o1": 200_000,
    "o3": 200_000,
}

DEFAULT_CONTEXT_WINDOW = 200_000  # 默认上下文窗口大小

# 自动压缩触发阈值 —— 上下文达到窗口的 80% 时触发
AUTO_COMPACT_THRESHOLD = 0.80

# 阻塞极限阈值 —— 上下文达到窗口的 95% 时拒绝新消息（为 /compact 预留空间）
BLOCKING_LIMIT_THRESHOLD = 0.95

ESCALATED_MAX_TOKENS = 64_000  # 升级后的最大输出 token 数


def get_context_window_size(model_id: str) -> int:
    """根据模型 ID 获取上下文窗口大小。"""
    for prefix, size in MODEL_CONTEXT_WINDOWS.items():
        if model_id.startswith(prefix):
            return size
    return DEFAULT_CONTEXT_WINDOW


def calculate_token_warning_state(
    token_count: int,
    model_id: str,
) -> dict[str, bool]:
    """计算 token 警告状态 —— 是否触发自动压缩或到达阻塞极限。"""
    window = get_context_window_size(model_id)
    return {
        "is_auto_compact_threshold": token_count >= int(window * AUTO_COMPACT_THRESHOLD),
        "is_at_blocking_limit": token_count >= int(window * BLOCKING_LIMIT_THRESHOLD),
    }


def get_final_context_tokens(messages: list[dict[str, Any]]) -> int:
    """从最后一条 assistant 消息的 usage 中获取上下文 token 数。

    如果无可用 usage 数据，回退到估算方式。
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            usage = msg.get("usage", {})
            if usage and isinstance(usage, dict):
                return (
                    int(usage.get("input_tokens", 0))
                    + int(usage.get("cache_read_input_tokens", 0))
                    + int(usage.get("cache_creation_input_tokens", 0))
                )
            break
    return estimate_messages_tokens(messages)
