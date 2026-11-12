"""把一条 SSE 帧流原样搬到 WebSocket 上。

**为什么常驻流要改走 WebSocket**：浏览器对同一 origin 的 HTTP/1.1 并发连接有
硬上限（Chromium 写死 6 条，见 ``net/socket/client_socket_pool_manager.cc``）。
应用常驻着好几条长连接就把名额占满了，普通请求（文件树、聊天）只能排队 ——
用户看到的就是「一调某个工具，整个界面全卡住」。

WebSocket 在 Chromium 里走的是**独立的 socket pool**（每组上限 255，全局每池
256），不跟普通请求抢那 6 条名额。于是把常驻流搬过来，额度就空出来了。

**帧格式不变**：SSE 帧本来就是文本（``event: x\\ndata: {...}\\n\\n``），WebSocket
文本帧也是文本。换的只是承载它的连接，不是协议 —— 客户端那套 SSE 解析器照用。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def serve_event_stream(
    websocket: WebSocket,
    events: AsyncGenerator[str, None],
) -> None:
    """接上连接，把 ``events`` 产出的帧逐条推过去，直到客户端断开。

    断开必须**立刻**察觉到：浏览器通道一断，引擎就该知道「桌面端不在场」，
    让等待中的 browser_* 工具马上失败，而不是各自耗到超时。所以除了发送任务，
    还并行挂一个「等断开」的任务 —— 光靠发送失败来发现，空闲流能拖十几秒。
    """
    await websocket.accept()
    sender = asyncio.create_task(_send_frames(websocket, events))
    watcher = asyncio.create_task(_watch_disconnect(websocket))
    try:
        done, pending = await asyncio.wait(
            {sender, watcher},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning("event stream ended with %r", exc, exc_info=exc)
    finally:
        sender.cancel()
        watcher.cancel()
        await asyncio.gather(sender, watcher, return_exceptions=True)
        # 生成器被取消时它自己的 finally 会跑（退订、清桥接）；这里再兜一次关闭，
        # 免得「订阅还挂在总线上」这种尾巴留着。
        await events.aclose()


async def _send_frames(websocket: WebSocket, events: AsyncGenerator[str, None]) -> None:
    async for frame in events:
        await websocket.send_text(frame)


async def _watch_disconnect(websocket: WebSocket) -> None:
    """等客户端断开。

    客户端往这条连接上不发东西，所以正常情况下这里一直挂着；它一断（收到
    ``websocket.disconnect``）就抛 ``WebSocketDisconnect``，由调用方收摊。
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
