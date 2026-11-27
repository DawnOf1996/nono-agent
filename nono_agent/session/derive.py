"""derive_messages —— 从事件流确定性重建消息数组（事件溯源核心）。

磁盘上只有事件；发给 LLM 的消息数组由本模块从事件流折叠出来。折叠是纯函数：
同一事件流在任何进程/平台产出字节级一致的结果。

「派生消息」是后端中立的中间形态（``{role, content}``，system 额外带 ``subtype``），
与引擎 ``Message`` 对象之间通过 ``messages_to_derived`` / ``derived_to_messages``
互转，两者互为逆运算 —— compact 边界事件携带的保留窗口就用这一形态存放。
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_RESULT,
    EVENT_USER_MESSAGE,
    KNOWN_EVENT_TYPES,
    SessionEvent,
)

COMPACT_BOUNDARY_SUBTYPE = "compact_boundary"


class ReconstructionError(Exception):
    """事件流无法重建为消息，或重建结果与实际请求不一致。"""


# ---------------------------------------------------------------------------
# Message ⇄ 派生消息
# ---------------------------------------------------------------------------


def message_to_derived(msg: Message) -> dict[str, Any] | None:
    """把引擎 Message 转为派生消息形态。

    Args:
        msg: 引擎消息对象。

    Returns:
        派生消息字典；对不参与模型上下文的消息类型（Tombstone、工具摘要等）返回 None。
    """
    if isinstance(msg, UserMessage):
        return {"role": "user", "content": msg.message.get("content")}
    if isinstance(msg, AssistantMessage):
        return {"role": "assistant", "content": msg.message.get("content")}
    if isinstance(msg, SystemMessage):
        return {"role": "system", "subtype": msg.subtype, "content": msg.content}
    return None


def messages_to_derived(messages: list[Message]) -> list[dict[str, Any]]:
    """批量转换为派生消息，跳过不参与上下文的消息。

    Args:
        messages: 引擎消息列表。

    Returns:
        派生消息列表。
    """
    derived: list[dict[str, Any]] = []
    for msg in messages:
        item = message_to_derived(msg)
        if item is not None:
            derived.append(item)
    return derived


def derived_to_messages(derived: list[dict[str, Any]]) -> list[Message]:
    """把派生消息还原为引擎 Message 对象（``messages_to_derived`` 的逆运算）。

    Args:
        derived: 派生消息列表。

    Returns:
        引擎消息列表；无法识别的 role 被忽略。
    """
    messages: list[Message] = []
    for item in derived:
        role = item.get("role")
        if role == "user":
            messages.append(
                UserMessage(message={"role": "user", "content": item.get("content")})
            )
        elif role == "assistant":
            messages.append(
                AssistantMessage(
                    message={"role": "assistant", "content": item.get("content")}
                )
            )
        elif role == "system":
            messages.append(
                SystemMessage(
                    subtype=item.get("subtype", ""),
                    content=item.get("content", ""),
                )
            )
    return messages


# ---------------------------------------------------------------------------
# 事件流 → 消息
# ---------------------------------------------------------------------------


def derive_messages(events: list[SessionEvent]) -> list[dict[str, Any]]:
    """把事件流确定性折叠为派生消息数组。

    折叠规则：

    - ``user/message`` → user
    - ``assistant/message`` → assistant（content 已含 tool_use blocks）
    - ``tool/result`` → user ``tool_result`` 消息；**连续**的 tool/result 事件并入同一条
      消息（并行工具调用的结果在 API 协议里必须放在一条 user 消息中）
    - ``compact/boundary`` → 丢弃此前全部消息，整体重置为事件携带的 ``kept`` 窗口
    - 其余已知事件（turn/step/request-header/chunk/tool-call/usage 等）不参与折叠

    Args:
        events: 事件列表（按 seq 排序后折叠，调用方无需预排序）。

    Returns:
        派生消息数组。

    Raises:
        ReconstructionError: 遇到本版本不认识且未标 ``ignorable`` 的事件。
    """
    messages: list[dict[str, Any]] = []
    # 上一条折叠出的消息是否是 tool/result 产出的、后续结果可继续并入的消息
    tool_results_open = False

    for ev in sorted(events, key=lambda e: e.seq):
        if ev.type == EVENT_USER_MESSAGE:
            messages.append({"role": "user", "content": ev.data.get("content")})
            tool_results_open = False
        elif ev.type == EVENT_ASSISTANT_MESSAGE:
            messages.append({"role": "assistant", "content": ev.data.get("content")})
            tool_results_open = False
        elif ev.type == EVENT_TOOL_RESULT:
            block = {
                "type": "tool_result",
                "tool_use_id": ev.data.get("call_id"),
                "content": ev.data.get("result"),
                "is_error": ev.data.get("is_error", False),
            }
            if tool_results_open:
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
                tool_results_open = True
        elif ev.type == EVENT_COMPACT_BOUNDARY:
            kept = ev.data.get("kept")
            messages = [dict(m) for m in kept] if isinstance(kept, list) else []
            tool_results_open = False
        elif ev.type not in KNOWN_EVENT_TYPES and not ev.ignorable:
            raise ReconstructionError(
                f"unknown non-ignorable event '{ev.type}' at seq={ev.seq}; "
                "the log was written by a newer build"
            )
    return messages


def fold_request_header(events: list[SessionEvent]) -> dict[str, Any] | None:
    """折叠出「当前生效」的请求配置快照。

    Args:
        events: 事件列表。

    Returns:
        最后一条 ``request/header`` 的 header 载荷；日志中没有则为 None。
    """
    header: dict[str, Any] | None = None
    for ev in sorted(events, key=lambda e: e.seq):
        if ev.type == EVENT_REQUEST_HEADER:
            candidate = ev.data.get("header")
            if isinstance(candidate, dict):
                header = candidate
    return header


def verify_reconstruction(
    events: list[SessionEvent], messages: list[dict[str, Any]]
) -> None:
    """校验「事件流重建 == 给定消息数组」。

    Args:
        events: 事件列表。
        messages: 实际运行时状态的派生消息数组。

    Raises:
        ReconstructionError: 两者不一致（log-reconstruction desync），
            错误信息定位到第一条不同的消息，便于回答「哪次状态变更没进日志」。
    """
    derived = derive_messages(events)
    if _canonical(derived) == _canonical(messages):
        return

    detail = _first_difference(derived, messages)
    raise ReconstructionError(f"log-reconstruction desync: {detail}")


def _first_difference(
    derived: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> str:
    """定位第一处差异，作为 desync 报错的诊断信息。"""
    for index in range(min(len(derived), len(actual))):
        if _canonical([derived[index]]) != _canonical([actual[index]]):
            return (
                f"messages[{index}] differs; "
                f"derived={_preview(derived[index])} actual={_preview(actual[index])}"
            )
    return (
        f"message count differs; derived={len(derived)} actual={len(actual)}; "
        f"first extra={_preview((derived + actual)[min(len(derived), len(actual))])}"
    )


def _preview(message: dict[str, Any], limit: int = 200) -> str:
    text = _canonical([message])
    return text if len(text) <= limit else text[:limit] + "…"


def _canonical(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, sort_keys=True, ensure_ascii=False)


__all__ = [
    "COMPACT_BOUNDARY_SUBTYPE",
    "ReconstructionError",
    "derive_messages",
    "derived_to_messages",
    "fold_request_header",
    "message_to_derived",
    "messages_to_derived",
    "verify_reconstruction",
]
