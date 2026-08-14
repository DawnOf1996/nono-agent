"""统一消息存储 —— 替代 TS 版的三数组模式。

TS 版有三个独立数组 (mutableMessages, messages, messagesForQuery)，
需要复杂的同步逻辑。本 MessageStore 提供单一数据源，
通过 view/snapshot 方法为不同消费者提供视图。

核心操作:
- append/extend: 添加消息
- snapshot_after_boundary(): 获取压缩边界后的消息（用于 API 调用）
- gc_before_boundary(): 清理压缩边界前的旧消息
- remove_by_uuid(): 支持 Tombstone 清理
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from nono_agent.core.types import (
    AssistantMessage,
    Message,
    SystemMessage,
)


class MessageStore:
    """统一消息存储 —— 带压缩边界感知的消息管理器。"""

    def __init__(self, initial: Sequence[Message] = ()) -> None:
        self._messages: list[Message] = list(initial)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def append(self, msg: Message) -> None:
        """添加单条消息。"""
        self._messages.append(msg)

    def extend(self, msgs: Sequence[Message]) -> None:
        """批量添加消息。"""
        self._messages.extend(msgs)

    def remove_by_uuid(self, target_uuid: str) -> bool:
        """按 UUID 移除消息 —— 用于 Tombstone 清理。"""
        for i, msg in enumerate(self._messages):
            if getattr(msg, "uuid", None) == target_uuid:
                del self._messages[i]
                return True
        return False

    def replace(self, new_messages: list[Message]) -> None:
        """Replace all messages (used after compact boundary rebuild)."""
        self._messages = new_messages

    # ------------------------------------------------------------------
    # Views / Snapshots
    # ------------------------------------------------------------------

    def snapshot_after_boundary(self) -> list[Message]:
        """Return a shallow copy of messages after the last compact boundary.

        This replaces getMessagesAfterCompactBoundary() + [...messages] shallow copy.
        The returned list is a new list (safe to mutate) but the message objects
        are shared references (mutations to messages are visible in the store).
        """
        boundary_idx = self._find_last_compact_boundary()
        return list(self._messages[boundary_idx:])

    def all_messages(self) -> list[Message]:
        """Return a shallow copy of all messages."""
        return list(self._messages)

    # ------------------------------------------------------------------
    # Garbage Collection
    # ------------------------------------------------------------------

    def gc_before_boundary(self) -> int:
        """Remove all messages before the last compact boundary.

        Returns the number of messages removed.
        This is called when a compact_boundary message is processed,
        to release pre-compaction messages for garbage collection.
        """
        boundary_idx = self._find_last_compact_boundary()
        if boundary_idx > 0:
            removed = boundary_idx
            del self._messages[:boundary_idx]
            return removed
        return 0

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def find_last(
        self,
        *types: type,
    ) -> Message | None:
        """Find the last message matching any of the given types."""
        for msg in reversed(self._messages):
            if isinstance(msg, types):
                return msg
        return None

    def count_by_type(self, *types: type) -> int:
        """Count messages matching any of the given types."""
        return sum(1 for msg in self._messages if isinstance(msg, types))

    def get_tool_use_blocks_from_assistants(
        self,
        assistant_messages: list[AssistantMessage],
    ) -> list[dict[str, Any]]:
        """Extract tool_use blocks from a list of assistant messages."""
        blocks: list[dict[str, Any]] = []
        for msg in assistant_messages:
            content = msg.message.get("content", [])
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    blocks.append(block)
        return blocks

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_last_compact_boundary(self) -> int:
        """Find the index of the last compact boundary system message."""
        for i in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[i]
            if isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
                return i
        return 0

    # ------------------------------------------------------------------
    # Sequence protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self) -> Iterator[Message]:
        return iter(self._messages)

    def __getitem__(self, index: int | slice) -> Message | list[Message]:
        return self._messages[index]

    def __bool__(self) -> bool:
        return bool(self._messages)

    def __repr__(self) -> str:
        return f"MessageStore(count={len(self._messages)})"
