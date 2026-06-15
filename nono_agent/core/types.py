"""核心类型定义 —— 基于 Pydantic v2 模型。

本文件是整个引擎的类型基石，定义了所有内部消息、流式事件、SDK 协议消息和工具结果。

核心设计：
- 使用 Literal["xxx"] 作为判别字段（discriminated union），实现类型安全的序列化/反序列化
- 内外部消息分离：Message 是引擎内部表示，SDKMessage 是对外协议格式
- 所有消息自动生成 uuid + timestamp，便于去重、排序和 Tombstone 清理
"""

from __future__ import annotations

import time
import uuid as _uuid
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


def _new_uuid() -> str:
    """生成唯一标识符，用于消息去重和 Tombstone 定位。"""
    return str(_uuid.uuid4())


def _now_ms() -> float:
    """获取当前毫秒时间戳，用于消息排序和耗时统计。"""
    return time.time() * 1000


# ---------------------------------------------------------------------------
# Content Blocks — 内容块（LLM 响应中的最小语义单元）
# ---------------------------------------------------------------------------
# 每个 AssistantMessage.message["content"] 是一个 ContentBlock 列表，
# LLM 可以在一次响应中混合文本、工具调用和思考过程。


class TextBlock(BaseModel):
    """纯文本内容块 —— LLM 生成的普通文本回复。"""

    type: Literal["text"] = "text"
    text: str  # 文本内容，支持流式增量拼接


class ToolUseBlock(BaseModel):
    """工具调用内容块 —— LLM 请求调用某个工具。

    当 LLM 决定使用工具时，会在 content 中生成此块。
    引擎解析后交给 ToolExecutor 执行，并将结果以 ToolResultContentBlock 回传。
    """

    type: Literal["tool_use"] = "tool_use"
    id: str                     # 工具调用唯一 ID，用于关联 tool_result
    name: str                   # 工具名称，对应 ToolRegistry 中注册的名称
    input: dict[str, Any]       # 工具输入参数，结构由工具的 input_schema 定义


class ToolResultContentBlock(BaseModel):
    """工具执行结果内容块 —— 放在 UserMessage 中回传给 LLM。

    工具执行完成后，引擎将结果封装为此块，放入一个合成的 UserMessage，
    使 LLM 能在下一轮看到工具的输出。
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str                         # 关联对应的 ToolUseBlock.id
    content: str | list[dict[str, Any]]      # 执行结果（纯文本或富内容）
    is_error: bool = False                   # 工具是否执行出错


class ThinkingBlock(BaseModel):
    """扩展思考内容块 —— Claude 模型的 extended thinking 输出。

    仅在 Anthropic 模型启用 thinking 时出现，包含模型的推理过程。
    在 microcompact 阶段会被从旧消息中剥离以节省 token。
    """

    type: Literal["thinking"] = "thinking"
    thinking: str                  # 思考内容文本
    signature: str | None = None   # 服务端签名，用于验证思考内容未被篡改


class RedactedThinkingBlock(BaseModel):
    """加密思考内容块 —— 服务端加密的思考内容（安全敏感场景）。

    当 Anthropic 的安全系统检测到敏感内容时，会将思考块替换为此加密版本。
    """

    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str  # 加密后的思考内容


# 所有内容块的联合类型 —— 一个 AssistantMessage 的 content 字段就是 list[ContentBlock]
ContentBlock = Union[
    TextBlock,
    ToolUseBlock,
    ToolResultContentBlock,
    ThinkingBlock,
    RedactedThinkingBlock,
]


# ---------------------------------------------------------------------------
# Usage — Token 用量统计
# ---------------------------------------------------------------------------
# 每次 API 响应都会返回 usage 信息，引擎通过 CostTracker 累积统计。


class Usage(BaseModel):
    """Token 用量计数器 —— 跟踪每次 API 调用的四类 token 消耗。

    四类 token：
    - input_tokens: 普通输入 token
    - output_tokens: 输出 token（最贵）
    - cache_read_input_tokens: 命中 prompt cache 的 token（最便宜）
    - cache_creation_input_tokens: 创建 prompt cache 的 token（略贵于普通输入）
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """所有 token 类型的总和。"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        """支持 usage_a + usage_b 累加语法，用于跨多次 API 调用的总量统计。"""
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
        )


EMPTY_USAGE = Usage()  # 零值常量，用于初始化累加器


# ---------------------------------------------------------------------------
# Messages (internal representation) — 内部消息类型
# ---------------------------------------------------------------------------
# 这些是引擎内部流转的消息，与对外 SDK 协议格式分离。
# 引擎的 agentic loop 操作的是 Message，最后通过 QueryEngine._to_sdk_messages() 转为 SDKMessage。


class UserMessage(BaseModel):
    """用户消息 —— 包括真实用户输入和引擎合成的消息（如 tool_result）。

    合成的 UserMessage 场景：
    - 工具执行结果：tool_use_result 字段标记
    - 恢复提示（max_output_tokens recovery）：is_meta=True
    - 压缩摘要（context collapse summary）：is_compact_summary=True
    """

    type: Literal["user"] = "user"
    uuid: str = Field(default_factory=_new_uuid)         # 唯一标识，用于 Tombstone 定位
    timestamp: float = Field(default_factory=_now_ms)    # 创建时间戳
    message: dict[str, Any]  # {"role": "user", "content": [...]} — API 格式的消息体
    tool_use_result: str | None = None                   # 非 None 表示这是工具结果消息
    source_tool_assistant_uuid: str | None = None        # 关联发起工具调用的 assistant 消息
    is_meta: bool = False                                # 引擎合成的元消息（如恢复提示）
    is_compact_summary: bool = False                     # 上下文压缩生成的摘要消息
    is_visible_in_transcript_only: bool = False          # 仅写入 transcript，不发送给 LLM


class AssistantMessage(BaseModel):
    """助手消息 —— LLM 的完整响应。

    message 字段包含 API 原始响应：
    - content: list[ContentBlock] — 文本 + 工具调用 + 思考
    - usage: dict — token 用量
    - stop_reason: "end_turn" | "tool_use" | "max_tokens" | "stop_sequence"
    """

    type: Literal["assistant"] = "assistant"
    uuid: str = Field(default_factory=_new_uuid)
    timestamp: float = Field(default_factory=_now_ms)
    message: dict[str, Any]  # {"role": "assistant", "content": [...], "usage": {...}, "stop_reason": ...}
    api_error: str | None = None            # API 调用出错时的错误信息
    is_api_error_message: bool = False      # 是否是 API 错误重试时生成的占位消息


class SystemMessage(BaseModel):
    """系统消息 —— 引擎自身生成的控制消息。

    用途包括：
    - compact_boundary: 标记上下文压缩边界，MessageStore 据此做 GC
    - api_retry: API 重试通知
    - tool_result: 工具执行进度
    - loop_terminal: agentic loop 终止信号
    """

    type: Literal["system"] = "system"
    subtype: str = ""  # 消息子类型: "compact_boundary", "api_error", "api_retry", "tool_result", "loop_terminal" 等
    uuid: str = Field(default_factory=_new_uuid)
    timestamp: float = Field(default_factory=_now_ms)
    content: str = ""                          # 消息文本内容
    compact_metadata: dict[str, Any] | None = None  # 压缩操作的元数据（原始消息数、节省 token 数等）
    retry_attempt: int | None = None           # API 重试当前次数
    max_retries: int | None = None             # API 最大重试次数
    retry_in_ms: int | None = None             # 重试等待时间
    error: dict[str, Any] | None = None        # 错误详情


class AttachmentMessage(BaseModel):
    """附件消息 —— 携带文件变更、队列命令、记忆等额外上下文。"""

    type: Literal["attachment"] = "attachment"
    uuid: str = Field(default_factory=_new_uuid)
    timestamp: float = Field(default_factory=_now_ms)
    attachment: dict[str, Any]  # {"type": "edited_text_file" | "queued_command" | "memory" | ...}


class ProgressMessage(BaseModel):
    """进度消息 —— 工具执行进度、延迟结果等中间状态通知。"""

    type: Literal["progress"] = "progress"
    uuid: str = Field(default_factory=_new_uuid)
    timestamp: float = Field(default_factory=_now_ms)
    content: str = ""


# 所有内部消息类型的联合类型 —— agentic loop 操作的消息都是 Message
# 通过 type 字段判别具体类型，避免 isinstance 链
Message = Union[
    UserMessage,
    AssistantMessage,
    SystemMessage,
    AttachmentMessage,
    ProgressMessage,
]


# ---------------------------------------------------------------------------
# Stream Events — 流式事件序列
# ---------------------------------------------------------------------------
# LLM 流式响应期间产生的事件序列，遵循 Anthropic 的 SSE 事件协议：
#   message_start -> [content_block_start -> delta* -> content_block_stop]* -> message_delta -> message_stop
# 引擎将这些事件透传给 CLI/Server 适配器，实现实时流式输出。


class MessageStartEvent(BaseModel):
    """消息开始事件 —— 新的 API 响应开始流式传输。"""

    type: Literal["message_start"] = "message_start"
    message: dict[str, Any]  # 包含初始 usage 信息（输入 token 数）


class ContentBlockStartEvent(BaseModel):
    """内容块开始事件 —— 一个新的内容块（文本/工具调用/思考）开始生成。"""

    type: Literal["content_block_start"] = "content_block_start"
    index: int                         # 内容块在消息中的索引位置
    content_block: dict[str, Any]      # 内容块初始内容


class ContentBlockDeltaEvent(BaseModel):
    """内容块增量事件 —— 每个 delta 携带一小片新增内容。"""

    type: Literal["content_block_delta"] = "content_block_delta"
    index: int                  # 对应内容块索引
    delta: dict[str, Any]       # 增量内容: {"type": "text_delta", "text": "..."} 或 {"type": "input_json_delta", ...}


class ContentBlockStopEvent(BaseModel):
    """内容块结束事件 —— 一个内容块生成完毕。"""

    type: Literal["content_block_stop"] = "content_block_stop"
    index: int  # 对应内容块索引


class MessageDeltaEvent(BaseModel):
    """消息级增量事件 —— 包含最终 usage 和 stop_reason。"""

    type: Literal["message_delta"] = "message_delta"
    delta: dict[str, Any]  # {"stop_reason": "end_turn" | "tool_use" | "max_tokens" | ...}
    usage: dict[str, Any] = Field(default_factory=dict)  # 最终输出 token 统计


class MessageStopEvent(BaseModel):
    """消息结束事件 —— 整个流式响应完成。"""

    type: Literal["message_stop"] = "message_stop"


# 所有流式事件的联合类型
StreamEvent = Union[
    MessageStartEvent,
    ContentBlockStartEvent,
    ContentBlockDeltaEvent,
    ContentBlockStopEvent,
    MessageDeltaEvent,
    MessageStopEvent,
]


class RequestStartEvent(BaseModel):
    """API 请求开始信号 —— 在调用 LLM API 前发出，用于通知 UI 开始“思考”。"""

    type: Literal["stream_request_start"] = "stream_request_start"


# ---------------------------------------------------------------------------
# Tool Result — 工具执行结果
# ---------------------------------------------------------------------------


class ToolResult(BaseModel):
    """工具执行结果 —— ToolExecutor 返回给引擎的结果数据。

    content 可以是纯文本或富内容（如图片 base64）。
    is_error=True 时 LLM 会看到错误信息并可能调整策略。
    """

    tool_use_id: str
    content: str | list[dict[str, Any]]
    is_error: bool = False
    tool_name: str = ""


# ---------------------------------------------------------------------------
# Tombstone — 墓碑消息（流式 Fallback 孤儿消息清理机制）
# ---------------------------------------------------------------------------
# 当流式输出触发 Fallback（如主模型不可用切换备用模型），
# 已吐出的半成品 AssistantMessage 成为“孤儿”，需要通知 UI 移除。
# TombstoneMessage 就是这个控制信号：告诉 UI 和 transcript “删除这条消息”。


class TombstoneMessage(BaseModel):
    """墓碑消息 —— 通知 UI 和 transcript 删除先前已发出的消息。"""

    type: Literal["tombstone"] = "tombstone"
    message: Message  # 需要被移除的消息对象（通过 uuid 匹配）


# ---------------------------------------------------------------------------
# Tool Use Summary — 工具调用摘要（由小模型异步生成）
# ---------------------------------------------------------------------------
# 当一次响应中包含多个工具调用时，引擎会启动后台任务调用小模型（如 Haiku）
# 生成一段摘要，帮助 LLM 在后续轮次中快速了解之前的工具操作结果。


class ToolUseSummaryMessage(BaseModel):
    """工具调用摘要消息 —— 由小模型异步生成的工具使用总结。"""

    type: Literal["tool_use_summary"] = "tool_use_summary"
    uuid: str = Field(default_factory=_new_uuid)
    summary: str                                        # 摘要文本
    preceding_tool_use_ids: list[str] = Field(default_factory=list)  # 被摘要的工具调用 ID 列表


# ---------------------------------------------------------------------------
# SDK Messages — 对外协议格式
# ---------------------------------------------------------------------------
# 这些是 QueryEngine.submit_message() 产出给外部消费者的消息格式。
# CLI 和 Server 适配器都消费这些消息，区别仅在于交付方式：
#   - CLI: 本地 async for 消费，Rich 渲染到终端
#   - Server: 通过 SSE/WebSocket 推送给远程 Web 客户端


class SDKAssistantMessage(BaseModel):
    """SDK 协议格式的助手消息。"""

    type: Literal["assistant"] = "assistant"
    message: dict[str, Any]                    # 完整的 API 响应体
    session_id: str                             # 所属会话 ID
    parent_tool_use_id: str | None = None       # 父工具调用 ID（子代理场景）
    uuid: str = Field(default_factory=_new_uuid)


class SDKUserMessageReplay(BaseModel):
    """SDK 协议格式的用户消息回放 —— 可选地将用户输入也回显给消费者。"""

    type: Literal["user"] = "user"
    message: dict[str, Any]
    session_id: str
    parent_tool_use_id: str | None = None
    uuid: str = Field(default_factory=_new_uuid)
    timestamp: float = Field(default_factory=_now_ms)
    is_replay: bool = True       # 标记这是回放消息而非新消息
    is_synthetic: bool = False   # 是否是引擎合成的用户消息


class SDKStreamEventMessage(BaseModel):
    """SDK 协议格式的流式事件 —— 包装 StreamEvent 供远程客户端消费。"""

    type: Literal["stream_event"] = "stream_event"
    event: dict[str, Any]
    session_id: str
    parent_tool_use_id: str | None = None
    uuid: str = Field(default_factory=_new_uuid)


class SDKSystemMessage(BaseModel):
    """SDK 协议格式的系统消息 —— 通知客户端会话状态变化。"""

    type: Literal["system"] = "system"
    subtype: str  # 子类型: "init"(会话初始化), "compact_boundary"(压缩边界), "api_retry"(重试通知), "request_start"
    session_id: str
    uuid: str = Field(default_factory=_new_uuid)
    # 以下字段根据 subtype 可选填充
    compact_metadata: dict[str, Any] | None = None
    attempt: int | None = None
    max_retries: int | None = None
    retry_delay_ms: int | None = None
    error_status: int | None = None
    error: str | None = None
    content: str | None = None  # 系统消息内容（如 tool_result 完成通知）


class SDKToolUseSummary(BaseModel):
    """SDK 协议格式的工具调用摘要。"""

    type: Literal["tool_use_summary"] = "tool_use_summary"
    summary: str
    preceding_tool_use_ids: list[str]
    session_id: str
    uuid: str = Field(default_factory=_new_uuid)


class SDKResultMessage(BaseModel):
    """SDK 协议格式的最终结果消息 —— 每轮 submit_message() 的最后一条消息。

    包含完整的统计信息：耗时、轮数、成本、token 用量、错误列表。
    客户端收到此消息即知本轮对话已完成。
    """

    type: Literal["result"] = "result"
    subtype: str  # "success" | "error_max_turns" | "error_max_budget_usd" | "error_during_execution" 等
    is_error: bool = False             # 是否为错误结束
    duration_ms: float = 0             # 总耗时（毫秒）
    duration_api_ms: float = 0         # API 调用耗时（毫秒，不含工具执行时间）
    num_turns: int = 0                 # agentic loop 迭代轮数
    result: str = ""                   # 最终结果文本（从最后一条 assistant 消息提取）
    stop_reason: str | None = None     # 循环终止原因
    session_id: str = ""               # 所属会话 ID
    total_cost_usd: float = 0          # 本轮累计美元费用
    usage: Usage = Field(default_factory=Usage)         # 累计 token 用量
    model_usage: dict[str, Any] = Field(default_factory=dict)  # 按模型分项的用量统计
    permission_denials: list[dict[str, Any]] = Field(default_factory=list)  # 权限拒绝记录
    errors: list[str] = Field(default_factory=list)     # 错误信息列表
    structured_output: Any | None = None                # JSON Schema 结构化输出（如配置了）
    uuid: str = Field(default_factory=_new_uuid)


# SDK 协议消息联合类型 —— QueryEngine.submit_message() 的返回类型
SDKMessage = Union[
    SDKAssistantMessage,
    SDKUserMessageReplay,
    SDKStreamEventMessage,
    SDKSystemMessage,
    SDKToolUseSummary,
    SDKResultMessage,
]


# ---------------------------------------------------------------------------
# InternalMessage — 内部消息联合类型
# ---------------------------------------------------------------------------
# agentic loop (core/loop.py) 能产出的所有消息类型。
# QueryEngine 接收 InternalMessage 后通过 _to_sdk_messages() 转换为 SDKMessage。
# 包含：普通消息、Tombstone、工具摘要、流式事件、请求开始信号。

InternalMessage = Union[
    Message,
    TombstoneMessage,
    ToolUseSummaryMessage,
    StreamEvent,
    RequestStartEvent,
]
