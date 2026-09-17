"""回放 —— 不调用 LLM，从事件流重建当时的 SDK 消息序列。

事件流记录了模型返回的每个增量与每条消息，所以「重看一次运行」不需要重跑模型：
按 seq 折叠即可。回放用于故障复现、UI 重绘和快照对比；它是只读的，
不写事件、不碰 ``MessageStore``。

与 ``derive_messages()`` 的分工：``derive_messages()`` 重建**发给模型的上下文**
（会话状态），本模块重建**消费者当时看到的输出流**（呈现）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from nono_agent.core.types import (
    SDKAssistantMessage,
    SDKMessage,
    SDKResultMessage,
    SDKStreamEventMessage,
    SDKSystemMessage,
    SDKUserMessageReplay,
    Usage,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_CHUNK,
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_PERMISSION_DENIAL,
    EVENT_TURN_END,
    EVENT_USAGE,
    EVENT_USER_MESSAGE,
    SessionEvent,
)


def replay_sdk_messages(
    events: list[SessionEvent],
    *,
    session_id: str = "",
    include_chunks: bool = False,
) -> Iterator[SDKMessage]:
    """把事件流回放为 SDK 消息序列。

    Args:
        events: 事件列表（内部按 seq 排序，调用方无需预排序）。
        session_id: 回放消息标注的会话 ID。
        include_chunks: 是否回放 ``assistant/chunk`` 增量。开启后可逐字重绘打字机
            效果，前提是当时以 ``log_assistant_chunks=True`` 运行。

    Yields:
        与当时输出同序的 SDKMessage。
    """
    ordered = sorted(events, key=lambda e: e.seq)
    usage_by_turn = _usage_by_turn(ordered)
    turn = 0

    for event in ordered:
        if event.type == EVENT_USER_MESSAGE:
            yield SDKUserMessageReplay(
                message={"role": "user", "content": event.data.get("content")},
                session_id=session_id,
            )
        elif event.type == EVENT_ASSISTANT_CHUNK and include_chunks:
            chunk = event.data.get("chunk")
            if isinstance(chunk, dict):
                yield SDKStreamEventMessage(event=chunk, session_id=session_id)
        elif event.type == EVENT_ASSISTANT_MESSAGE:
            yield SDKAssistantMessage(
                message={
                    "role": "assistant",
                    "content": event.data.get("content"),
                },
                session_id=session_id,
            )
        elif event.type == EVENT_COMPACT_BOUNDARY:
            metadata = event.data.get("metadata")
            yield SDKSystemMessage(
                subtype="compact_boundary",
                content=str(event.data.get("content", "")),
                compact_metadata=metadata if isinstance(metadata, dict) else None,
                session_id=session_id,
            )
        elif event.type == EVENT_PERMISSION_DENIAL:
            yield SDKSystemMessage(
                subtype="permission_denial",
                content=(
                    f"Denied {event.data.get('tool')}: {event.data.get('rule')}"
                ),
                session_id=session_id,
            )
        elif event.type == EVENT_TURN_END:
            turn = int(event.data.get("turn", turn + 1))
            reason = str(event.data.get("reason", "unknown"))
            is_error = reason not in ("COMPLETED", "completed")
            yield SDKResultMessage(
                subtype="success" if not is_error else f"error_{reason.lower()}",
                is_error=is_error,
                num_turns=turn,
                session_id=session_id,
                usage=usage_by_turn.get(turn, Usage()),
            )


def _usage_by_turn(ordered: list[SessionEvent]) -> dict[int, Usage]:
    """把 usage 事件按回合累加 —— 回放的 result 消息要带当时的累计用量。"""
    totals: dict[int, Usage] = {}
    turn = 1
    for event in ordered:
        if event.type == EVENT_USAGE:
            payload = event.data.get("usage")
            if isinstance(payload, dict):
                totals[turn] = totals.get(turn, Usage()) + Usage(**payload)
        elif event.type == EVENT_TURN_END:
            turn = int(event.data.get("turn", turn)) + 1
    return totals


def replay_transcript_text(events: list[SessionEvent]) -> str:
    """把事件流回放为纯文本对话记录（人读用）。

    Args:
        events: 事件列表。

    Returns:
        每行一条消息的文本记录。
    """
    lines: list[str] = []
    for event in sorted(events, key=lambda e: e.seq):
        if event.type == EVENT_USER_MESSAGE:
            lines.append(f"user: {_flatten(event.data.get('content'))}")
        elif event.type == EVENT_ASSISTANT_MESSAGE:
            lines.append(f"assistant: {_flatten(event.data.get('content'))}")
        elif event.type == EVENT_COMPACT_BOUNDARY:
            lines.append(f"system: {event.data.get('content', '')}")
    return "\n".join(lines)


def _flatten(content: Any) -> str:
    """把消息 content 压平为单行文本（忽略非文本块）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


__all__ = ["replay_sdk_messages", "replay_transcript_text"]
