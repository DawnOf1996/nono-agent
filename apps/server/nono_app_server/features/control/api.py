"""WS /app/control/ws：一条常驻连接承载「服务在否」和待办授权列表。

客户端本来靠两个定时器问服务端：15 秒一次 ``/health``，以及只要有会话在跑就
1 秒一次 ``/app/permissions``。这两件事都不需要问 —— 服务在不在，看这条连接
还通不通就行；授权列表在服务端每次增删时扇出即可。这条连接常驻，顺带兼作心跳：
真断开了客户端自己会看到，``: ping`` 只是为了让中间代理别把闲置连接掐掉。

**为什么是 WebSocket 而不是 SSE**：浏览器对同一 origin 的 HTTP/1.1 并发连接有
硬上限（Chromium 写死 6 条）。应用常驻着好几条长连接就把名额占满，普通请求只能
排队。常驻流改走 WebSocket 后落到浏览器**独立的 socket pool**，不再抢那几条。
帧格式原样保留（``event: x`` / ``data: {...}``），换的只是承载它的连接。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, WebSocket

from nono_app_server.common.ws_stream import serve_event_stream
from nono_app_server.engine.deps import get_session_manager_ws
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.permissions import service as permissions

router = APIRouter()

_PING_S = 15.0


def _frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.websocket("/control/ws")
async def control_ws(
    websocket: WebSocket,
    manager: SessionManager = Depends(get_session_manager_ws),
) -> None:
    """控制流的 WebSocket 端点。

    帧的形状与旧版 SSE 完全一样：先补两帧当前值（health / permissions），
    之后只在授权列表变化时推一帧，空闲时每 15 秒发一个注释帧保活。
    """
    await serve_event_stream(websocket, control_events(manager))


async def control_events(manager: SessionManager) -> AsyncGenerator[str, None]:
    changed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_change() -> None:
        # 授权流程可能从工作线程回调过来，跨线程时必须走回事件循环。
        try:
            loop.call_soon_threadsafe(changed.set)
        except RuntimeError:
            return

    unsubscribe = permissions.subscribe(on_change)
    try:
        # 先补两帧当前值：客户端连上就有数，不用等第一次变化。
        yield _frame("health", {"status": "ok", "sessions": len(manager.sessions)})
        yield _frame("permissions", {"permissions": permissions.list_pending()})
        while True:
            try:
                await asyncio.wait_for(changed.wait(), timeout=_PING_S)
            except TimeoutError:
                # 纯注释帧：只为保活，不是数据，客户端据此不刷新任何东西。
                yield ": ping\n\n"
                continue
            changed.clear()
            yield _frame("permissions", {"permissions": permissions.list_pending()})
    except asyncio.CancelledError:
        # 客户端断开：正常路径，不是错误。
        raise
    finally:
        unsubscribe()
