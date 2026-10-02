"""Token 计数与估算工具。

提供基于 tiktoken 的精确 token 计数和回退估算，
以及上下文窗口管理的辅助函数。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 懒加载 tiktoken：编码器初始化要读 BPE 词表，别拖慢 import。
_tiktoken_encoder: Any = None
# 已经试过并失败了就不再重试。词表是**首次使用时才下载**的，
# 离线/代理不通时会抛 requests 异常而不是 ImportError；没有这个标记的话，
# count_tokens 每调一次就重发一次 HTTP，而它每轮对话要被调几百次。
_tiktoken_unavailable = False


def _get_encoder() -> Any:
    """懒加载 tiktoken 编码器 —— 首次调用时初始化；不可用时返回 None。

    tiktoken 是声明的硬依赖，但它的 BPE 词表**不在包里**，首次 ``get_encoding``
    要联网下载。桌面端经常离线或走公司代理，下载失败会抛 ``requests`` 的异常 ——
    这里统一吞掉退回启发式估算，不能让它冒到 ``analyze_context_usage``
    （每条 assistant 消息都会调）把整轮对话带崩。
    """
    global _tiktoken_encoder, _tiktoken_unavailable
    if _tiktoken_unavailable:
        return None
    if _tiktoken_encoder is None:
        try:
            import tiktoken

            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            _tiktoken_unavailable = True
            logger.warning("tiktoken 未安装，token 估算退回 ~4 字符/token 启发式")
            return None
        except Exception as exc:  # 词表下载失败等：网络、代理、磁盘只读
            _tiktoken_unavailable = True
            logger.warning(
                "tiktoken 编码器不可用（%s），token 估算退回 ~4 字符/token 启发式", exc
            )
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


def _count_unknown_block(block: dict[str, Any]) -> int:
    """未知块类型的保守估计 —— 递归统计所有非空字段。

    宁可略高估也不要漏算：漏算会让 analyze_context_usage 低报占用，
    从而把 MicroCompact/AutoCompact 的触发阈值整体推迟。
    """
    total = 0
    for key, value in block.items():
        if key == "type" or value in (None, "", {}, []):
            continue
        if isinstance(value, str):
            total += count_tokens(value)
        elif isinstance(value, bool):
            total += 1
        elif isinstance(value, (int, float)):
            total += 1
        else:
            try:
                total += count_tokens(json.dumps(value, ensure_ascii=False))
            except (TypeError, ValueError):
                total += count_tokens(str(value))
    return total


def _count_tool_result_content(content: Any) -> int:
    """估算 tool_result.content 的 token 数。

    ``ToolResultContentBlock.content`` 的类型是 ``str | list[dict]``：
    纯文本走 str 分支；富内容（图片 base64、多段文本等）走 list 分支。
    历史实现只认 str，list 形态会整块漏算。
    """
    if not content:
        return 0
    if isinstance(content, str):
        return count_tokens(content)
    if isinstance(content, list):
        # 元素仍是内容块，递归复用同一套分派逻辑。
        return sum(_count_content_block(item) for item in content)
    try:
        return count_tokens(json.dumps(content, ensure_ascii=False))
    except (TypeError, ValueError):
        return count_tokens(str(content))


def _count_content_block(block: Any) -> int:
    """估算单个内容块的 token 数 —— 按 ``type`` 分派。

    覆盖 ``nono_agent/core/types.py`` 中 ContentBlock 联合类型的全部形状：

    ===================  ====================================
    块 type               取值字段
    ===================  ====================================
    ``text``              ``text``
    ``tool_use``          ``name`` + ``input``
    ``tool_result``       ``content``（str **或** 富内容 list）
    ``thinking``          ``thinking``（**不是** ``text``）
    ``redacted_thinking`` ``data``（**不是** ``text``）
    ===================  ====================================

    历史实现是无脑读 ``block["text"]``：thinking / redacted_thinking 因键名
    对不上而计 0，list 型 tool_result 因非 str 而整块漏算，导致上下文占用
    显著低估（实测 -33%），进而让压缩阈值全部偏晚触发。
    """
    if isinstance(block, str):
        return count_tokens(block)
    if not isinstance(block, dict):
        return count_tokens(str(block))

    block_type = block.get("type", "")

    if block_type == "thinking":
        # 扩展思考正文 —— 键名是 thinking，这是历史漏算的主因。
        return count_tokens(block.get("thinking", "") or "")

    if block_type == "redacted_thinking":
        # 服务端加密的思考内容，密文在 data 字段。
        return count_tokens(block.get("data", "") or "")

    if block_type == "tool_use":
        # 入参：流式累积期是 JSON 字符串，Messages API 形态是 dict，统一按 JSON 计数。
        raw_input = block.get("input", "")
        total = 0
        if raw_input:
            if isinstance(raw_input, str):
                total += count_tokens(raw_input)
            else:
                try:
                    total += count_tokens(json.dumps(raw_input, ensure_ascii=False))
                except (TypeError, ValueError):
                    total += count_tokens(str(raw_input))
        # 工具名本身也占 token（模型看到的 name 字段）
        name = block.get("name", "")
        if name:
            total += count_tokens(str(name))
        return total

    if block_type == "tool_result":
        return _count_tool_result_content(block.get("content", ""))

    if block_type == "text":
        text = block.get("text", "")
        return count_tokens(text) if text else 0

    return _count_unknown_block(block)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """估算单条消息的 token 数。

    包含 role、content blocks 和消息开销。
    支持 text / thinking / redacted_thinking / tool_use / tool_result
    以及富内容 tool_result（list）等全部内容块类型。
    """
    total = 4  # 消息开销（role + 分隔符等）
    role = message.get("role", "")
    total += count_tokens(role)

    content = message.get("content", "")
    if isinstance(content, str):
        total += count_tokens(content)
    elif isinstance(content, list):
        for block in content:
            total += _count_content_block(block)

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
