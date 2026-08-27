"""拒绝追踪 —— 对标 Claude Code permissionDenials。

每次权限拒绝被记录为 Denial，进入会话态并在查询结束时回灌到
``SDKResultMessage.permission_denials``，供 SDK 消费者透明展示。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Denial:
    """单次权限拒绝记录。"""

    tool_name: str
    reason: str
    input_params: dict[str, Any] = field(default_factory=dict)
    mode: str = "default"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "reason": self.reason,
            "input_params": self.input_params,
            "mode": self.mode,
            "timestamp": self.timestamp,
        }


class DenialTracker:
    """会话级拒绝追踪器。"""

    def __init__(self) -> None:
        self._denials: list[Denial] = []
        self._observers: list[Callable[[Denial], None]] = []

    def add_observer(self, observer: Callable[[Denial], None]) -> Callable[[], None]:
        """注册拒绝回调 —— 引擎用它把拒绝写入会话事件流。

        拒绝发生在工具执行内部，引擎观察不到，只能由本追踪器回调上报。

        Args:
            observer: 每次 ``record()`` 后同步调用的回调。

        Returns:
            注销该回调的函数。
        """
        self._observers.append(observer)

        def dispose() -> None:
            if observer in self._observers:
                self._observers.remove(observer)

        return dispose

    def record(
        self,
        tool_name: str,
        reason: str,
        input_params: dict[str, Any] | None = None,
        mode: str = "default",
    ) -> None:
        """记录一次拒绝并通知观察者。"""
        denial = Denial(
            tool_name=tool_name,
            reason=reason,
            input_params=input_params or {},
            mode=mode,
        )
        self._denials.append(denial)
        for observer in self._observers:
            observer(denial)

    def list(self) -> list[dict[str, Any]]:
        """返回所有拒绝记录（字典列表，可直接进入 SDK 契约）。"""
        return [d.to_dict() for d in self._denials]

    def count(self) -> int:
        return len(self._denials)

    def clear(self) -> None:
        self._denials.clear()

    def __len__(self) -> int:
        return len(self._denials)
