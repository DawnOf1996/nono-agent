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
# 上下文窗口辅助 —— 上下文大小配置与自动压缩阈值
# ---------------------------------------------------------------------------

# 模型上下文窗口的默认兜底值 —— 仅当配置（model_context）未提供时使用。
# 正式配置入口：ProviderConfig.model_context / EngineConfig.model_context。
DEFAULT_CONTEXT_WINDOW = 200_000

# 自动压缩触发阈值 —— 上下文达到窗口的 80% 时触发
AUTO_COMPACT_THRESHOLD = 0.80

# 阻塞极限阈值 —— 上下文达到窗口的 95% 时拒绝新消息（为 /compact 预留空间）
BLOCKING_LIMIT_THRESHOLD = 0.95

# 微压缩触发阈值 —— 上下文达到窗口的 50% 时开始细粒度压缩
MICROCOMPACT_THRESHOLD = 0.50

# 压缩回落目标 —— 触发压缩后尽量把上下文降回窗口的 60%
COMPACT_TARGET_RATIO = 0.60

ESCALATED_MAX_TOKENS = 64_000  # 升级后的最大输出 token 数


def resolve_context_window(model_context: int | None) -> int:
    """解析生效的上下文窗口大小 —— 显式配置优先，未配置时回退默认值。

    上下文大小属于**模型配置**（``ProviderConfig.model_context`` /
    ``EngineConfig.model_context``），由调用方解析后传入；不再基于模型 ID
    前缀查表猜测。
    """
    if model_context is None or model_context <= 0:
        return DEFAULT_CONTEXT_WINDOW
    return int(model_context)


def estimate_system_prompt_tokens(system_prompt: str) -> int:
    """估算系统提示词的 token 数。"""
    if not system_prompt:
        return 0
    return count_tokens(system_prompt)


def estimate_tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    """估算工具 API schema 的 token 数。

    每个工具 = 名称 + 描述 + 参数 schema 整体 JSON 序列化后计数，
    另加少量每次调用的固定开销。
    """
    if not tools:
        return 0
    total = 0
    for tool in tools:
        try:
            total += count_tokens(json.dumps(tool, ensure_ascii=False))
        except (TypeError, ValueError):
            total += count_tokens(str(tool))
        total += 8  # 单工具在请求中的固定开销（工具节标记等）
    return total


def estimate_request_tokens(
    messages: list[dict[str, Any]],
    system_prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """估算一次完整请求的 token 数 = 系统提示词 + 工具 schema + 对话消息。"""
    return (
        estimate_system_prompt_tokens(system_prompt)
        + estimate_tools_tokens(tools)
        + estimate_messages_tokens(messages)
    )


def analyze_context_usage(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
    model_context: int | None = None,
) -> dict[str, Any]:
    """分析当前上下文使用情况 —— 总量、占比与分项明细。

    分项包含系统提示词 / 工具 schema / 对话消息各占多少 token，
    以及相对配置窗口（``model_context``，未配置时回退默认值）的占比与
    自动压缩/阻塞阈值状态。
    """
    window = resolve_context_window(model_context)
    system_tokens = estimate_system_prompt_tokens(system_prompt)
    tools_tokens = estimate_tools_tokens(tools)
    messages_tokens = estimate_messages_tokens(messages)
    total = system_tokens + tools_tokens + messages_tokens

    def _ratio(part: int) -> float:
        return round(part / window, 4) if window > 0 else 0.0

    return {
        "context_window": window,
        "total_tokens": total,
        "usage_ratio": _ratio(total),
        "remaining_tokens": max(0, window - total),
        "system_prompt_tokens": system_tokens,
        "system_prompt_ratio": _ratio(system_tokens),
        "tools_tokens": tools_tokens,
        "tools_count": len(tools) if tools else 0,
        "tools_ratio": _ratio(tools_tokens),
        "messages_tokens": messages_tokens,
        "messages_ratio": _ratio(messages_tokens),
        "message_count": len(messages),
        "is_auto_compact_threshold": total >= int(window * AUTO_COMPACT_THRESHOLD),
        "is_at_blocking_limit": total >= int(window * BLOCKING_LIMIT_THRESHOLD),
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
