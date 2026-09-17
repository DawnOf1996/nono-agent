"""AbortSignal 取消信号管理。

提供类似 AbortController 的取消模式，基于 asyncio.Event 实现，
与 TS 版的 AbortController 接口兼容。

工作原理：
- AbortController 创建 AbortSignal 并持有其引用
- 外部代码调用 controller.abort() 触发信号
- 内部代码通过 signal.aborted 检查或 await signal.wait() 阻塞等待
"""

from __future__ import annotations

import asyncio


class AbortSignal:
    """取消信号 —— 可被检查或等待。

    内部使用 asyncio.Event 实现，支持同步检查 (aborted) 和异步等待 (wait())。
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()    # 底层事件
        self._reason: str | None = None  # 取消原因

    @property
    def aborted(self) -> bool:
        """是否已被取消。"""
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        """取消原因。"""
        return self._reason

    def _set(self, reason: str | None = None) -> None:
        """内部方法 —— 触发取消信号。"""
        self._reason = reason
        self._event.set()

    async def wait(self) -> None:
        """阻塞等待直到取消信号被触发。"""
        await self._event.wait()


class AbortController:
    """取消控制器 —— 控制 AbortSignal，允许外部代码触发取消。

    用法::

        controller = AbortController()
        if controller.signal.aborted:
            ...
        controller.abort()  # 触发取消信号
    """

    def __init__(self) -> None:
        self.signal = AbortSignal()  # 关联的信号实例

    def abort(self, reason: str | None = None) -> None:
        """触发取消信号 —— 通知所有监听者停止操作。"""
        self.signal._set(reason)
