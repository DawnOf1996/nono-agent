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
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from typing import TypeVar

T = TypeVar("T")


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


async def iterate_with_abort(
    source: AsyncIterator[T],
    signal: AbortSignal | None,
) -> AsyncGenerator[T, None]:
    """消费异步迭代器，中止信号一置位就立刻收摊 —— 不等 source 自己结束。

    普通的 ``async for`` 会一直阻塞在 ``__anext__`` 上。模型流式输出时这一等可能
    是几十秒，期间**没有任何代码有机会读到** ``signal.aborted``：信号被置位了，
    却没人去看，于是中止看起来"毫无反应"。

    这里把「取下一个元素」和「等中止」放进同一个 ``asyncio.wait``，谁先回就处理谁。
    中止先到时撤掉挂起的读并 ``aclose()`` 源，让底层 HTTP 连接真正断开，而不是
    让一个读了一半的流挂到超时。

    只用局部状态（一个 waiter task + 传入的 signal），不持有任何模块级或跨会话
    引用 —— 因此中止会话 A 不会牵动会话 B。

    Args:
        source: 异步迭代器，通常是 provider 的流式响应生成器。
        signal: 中止信号。为 None 表示不设中止控制（照常消费到底）。

    Yields:
        source 依次产出的元素；中止后立即停止产出。
    """
    if signal is None:
        # 没有中止控制就不套竞速 —— 直接透传，别给不需要它的调用方添开销。
        async for item in source:
            yield item
        return

    if signal.aborted:
        return

    iterator = source.__aiter__()
    waiter = asyncio.ensure_future(signal.wait())
    try:
        while True:
            nxt = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait(
                {nxt, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            # 两者同时就绪时优先当作中止：宁停早停，也不要漏停。
            if waiter in done:
                nxt.cancel()
                with suppress(BaseException):
                    await nxt
                return
            try:
                item = nxt.result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        waiter.cancel()
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            # 正常跑完也要收尾：源生成器不关，SDK 的连接池会一直挂着半开的流。
            with suppress(BaseException):
                await aclose()
