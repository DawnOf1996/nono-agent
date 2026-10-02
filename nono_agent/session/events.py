"""SessionEvent —— 事件溯源会话日志的信封与事件类型目录。

对齐 deepseek-harness 的 SessionEvent：一条追加-only 事件流，每条同一信封
（seq / type / data / timestamp / ignorable）。新增事件类型一律标
``ignorable=True``，保证旧版本读新日志不崩（前向兼容）。
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from pydantic import BaseModel, Field

# —— 事件类型目录（对齐 deepseek-harness SessionEventMap，映射到 nono-agent Message）——
EVENT_TURN_START = "turn/start"
EVENT_TURN_END = "turn/end"
EVENT_STEP_START = "step/start"
EVENT_STEP_END = "step/end"
EVENT_REQUEST_HEADER = "request/header"
EVENT_USER_INPUT = "user/input"
EVENT_USER_MESSAGE = "user/message"
EVENT_ASSISTANT_CHUNK = "assistant/chunk"
EVENT_ASSISTANT_MESSAGE = "assistant/message"
EVENT_TOOL_CALL = "tool/call"
EVENT_TOOL_RESULT = "tool/result"
EVENT_COMPACT_BOUNDARY = "compact/boundary"
EVENT_API_RETRY = "api/retry"
EVENT_PERMISSION_DENIAL = "permission/denial"
EVENT_PERMISSION_ONCHANGE = "permission/onchange"
EVENT_PERMISSION_CHANGED = "permission/changed"
EVENT_MEMORY_WRITE = "memory/write"
EVENT_MEMORY_RETRIEVE = "memory/retrieve"
EVENT_USAGE = "usage"
# 工具结果给 UI 的结构化附件（文件差异等）。ignorable：不参与消息重建，
# 也就永远不会进模型上下文 —— 它只是给界面的。
EVENT_UI_TOOL_RESULT = "ui/tool-result"

# 本版本认识的全部事件类型。读日志时遇到不在此集合内且未标 ignorable 的事件
# 视为「更高版本写入的必读事件」，拒绝重建（见 derive_messages）。
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_TURN_START,
        EVENT_TURN_END,
        EVENT_STEP_START,
        EVENT_STEP_END,
        EVENT_REQUEST_HEADER,
        EVENT_USER_INPUT,
        EVENT_USER_MESSAGE,
        EVENT_ASSISTANT_CHUNK,
        EVENT_ASSISTANT_MESSAGE,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        EVENT_COMPACT_BOUNDARY,
        EVENT_API_RETRY,
        EVENT_PERMISSION_DENIAL,
        EVENT_PERMISSION_ONCHANGE,
        EVENT_PERMISSION_CHANGED,
        EVENT_MEMORY_WRITE,
        EVENT_MEMORY_RETRIEVE,
        EVENT_USAGE,
        EVENT_UI_TOOL_RESULT,
    }
)


class SessionEventSink(Protocol):
    """事件汇 —— 让 agentic loop 把自身造成的状态变更报告给会话日志。

    loop 在 ``MessageStore`` 上做的每一次替换、以及 step / api 重试边界，
    引擎都无法从 yield 出来的消息流里观察到，必须由 loop 主动上报，
    否则「日志重建 == 运行时状态」的不变量会被静默破坏。

    实现方为同步函数（引擎侧只入队，落盘在最近的 await 点发生）。
    """

    def __call__(self, type_: str, /, **data: Any) -> None:
        """上报一条事件。

        Args:
            type_: 事件类型（本模块的 ``EVENT_*`` 常量）。
            data: 事件载荷。
        """
        ...


# request/header 的 reason 取值
REQUEST_HEADER_REASON_INITIAL = "initial"
REQUEST_HEADER_REASON_RESUME = "resume"
REQUEST_HEADER_REASON_CHANGE = "change"

# 会话日志格式版本（写入 SessionHeader；版本不符拒读）
SESSION_FORMAT_VERSION = 1


class SessionEvent(BaseModel):
    """会话事件信封 —— 事件溯源的基本单元。"""

    seq: int = 0                                # 自增序号，定位用
    type: str                                   # 事件类型（见上方常量）
    data: dict[str, Any] = Field(default_factory=dict)   # 事件载荷
    timestamp: float = Field(default_factory=lambda: time.time() * 1000)
    ignorable: bool = False                     # 老版本不认识时允许跳过

    @classmethod
    def make(
        cls,
        seq: int,
        type: str,
        *,
        ignorable: bool = False,
        **data: Any,
    ) -> SessionEvent:
        """便捷构造。"""
        return cls(seq=seq, type=type, data=data, ignorable=ignorable)


def last_turn_number(events: list[SessionEvent]) -> int:
    """从事件流取出已出现过的最大 ``turn``（``turn/start`` / ``turn/end``）。

    resume 后续聊应从该值继续累加，而不是从 1 重计。
    """
    last = 0
    for ev in events:
        if ev.type not in (EVENT_TURN_START, EVENT_TURN_END):
            continue
        turn = ev.data.get("turn") if isinstance(ev.data, dict) else None
        if isinstance(turn, bool):
            continue
        if isinstance(turn, int) and turn > last:
            last = turn
        elif isinstance(turn, float) and turn == int(turn) and int(turn) > last:
            last = int(turn)
    return last


__all__ = [
    "EVENT_API_RETRY",
    "EVENT_ASSISTANT_CHUNK",
    "EVENT_ASSISTANT_MESSAGE",
    "EVENT_COMPACT_BOUNDARY",
    "EVENT_MEMORY_RETRIEVE",
    "EVENT_MEMORY_WRITE",
    "EVENT_PERMISSION_CHANGED",
    "EVENT_PERMISSION_DENIAL",
    "EVENT_PERMISSION_ONCHANGE",
    "EVENT_REQUEST_HEADER",
    "EVENT_STEP_END",
    "EVENT_STEP_START",
    "EVENT_TOOL_CALL",
    "EVENT_TOOL_RESULT",
    "EVENT_TURN_END",
    "EVENT_TURN_START",
    "EVENT_USAGE",
    "EVENT_USER_INPUT",
    "EVENT_USER_MESSAGE",
    "KNOWN_EVENT_TYPES",
    "REQUEST_HEADER_REASON_CHANGE",
    "REQUEST_HEADER_REASON_INITIAL",
    "REQUEST_HEADER_REASON_RESUME",
    "SESSION_FORMAT_VERSION",
    "SessionEvent",
    "SessionEventSink",
    "last_turn_number",
]
