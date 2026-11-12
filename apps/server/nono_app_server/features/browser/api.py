"""WS /app/browser/ws：桌面端渲染进程与引擎之间的常驻连接。

渲染进程连上这条连接，内置浏览器工具才知道「桌面端在场」；引擎把命令排进
总线，这里把命令推给渲染进程，渲染进程执行完再 POST 回 :func:`reply`。

与 ``/app/control/ws`` 同构（``asyncio.Event`` + ``call_soon_threadsafe``
把工作线程里的回调搬回事件循环），区别只在于这里带**回执**：命令不是单向通知，
而是「一问一答」。所以每帧都带 ``request_id``，渲染进程必须原样带回。

**为什么是 WebSocket 而不是 SSE**：常驻连接占着浏览器同一 origin 的 HTTP/1.1
名额（Chromium 每 host 只有 6 条），应用常驻着好几条长连接就把名额占满，普通
请求只能排队。WebSocket 走独立的 socket pool，不抢那几条。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, WebSocket
from pydantic import BaseModel

from nono_agent.browser import bridge
from nono_app_server.common.ws_stream import serve_event_stream

logger = logging.getLogger(__name__)

router = APIRouter()

_PING_S = 15.0


class BrowserReply(BaseModel):
    """渲染进程对一条命令的回执。

    ``request_id`` 原样带回总线里的那条；其余字段按 ``op`` 而定，
    这里只做形状兜底 —— 桌面端是可信的一端，不该因为多带一个字段就 422。
    """

    request_id: str
    ok: bool = True
    message: str = ""
    tab_id: str = ""
    title: str = ""
    url: str = ""
    bound: str = ""
    tabs: list[dict[str, Any]] | None = None


def _frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.websocket("/browser/ws")
async def browser_ws(websocket: WebSocket) -> None:
    """浏览器通道的 WebSocket 端点。

    连接本身就是「桌面端在场」的信号；连上先补一帧 ``ready``，让渲染进程立刻
    知道通道通了，不必干等到第一次变化。
    """
    await serve_event_stream(websocket, browser_events())


async def browser_events() -> AsyncGenerator[str, None]:
    changed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_change() -> None:
        # 引擎的工具跑在自己的事件循环上，跨循环时必须搬回来。
        try:
            loop.call_soon_threadsafe(changed.set)
        except RuntimeError:
            return

    unsubscribe = bridge.subscribe(on_change)
    detach = bridge.attach()
    logger.info("Browser bridge attached")
    try:
        # 先补一帧空命令：让渲染进程立刻知道「通道通了」，
        # 它可以据此把 hello/能力声明发上来，不必干等到第一次变化。
        yield _frame("ready", {"ok": True})
        while True:
            for item in bridge.take_pending():
                yield _frame("command", item)
            try:
                await asyncio.wait_for(changed.wait(), timeout=_PING_S)
            except TimeoutError:
                # 纯保活帧：客户端据此不刷新任何东西。
                yield ": ping\n\n"
                continue
            changed.clear()
    except asyncio.CancelledError:
        # 客户端断开是正常路径，不是错误。
        raise
    finally:
        detach()
        unsubscribe()
        # 连接断了，之前排队的命令不会再有人执行；让等待中的工具立刻拿到
        # 明确失败，而不是各自耗到超时。
        with contextlib.suppress(Exception):
            bridge.cancel_all("桌面端已断开")
        logger.info("Browser bridge detached")


@router.post("/browser/reply")
async def reply(body: BrowserReply) -> dict[str, Any]:
    """投递一条回执。找不到对应请求（已超时/已取消）时返回 ok=False。"""
    delivered = bridge.resolve(body.request_id, body.model_dump())
    return {"ok": delivered}


@router.get("/browser/status")
async def status() -> dict[str, Any]:
    """通道状态。给排障用，不含页面数据。"""
    return bridge.stats()
