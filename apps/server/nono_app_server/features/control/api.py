"""GET /app/control/stream：一条常驻 SSE 承载「服务在否」和待办授权列表。

客户端本来靠两个定时器问服务端：15 秒一次 ``/health``，以及只要有会话在跑就
1 秒一次 ``/app/permissions``。这两件事都不需要问 —— 服务在不在，看这条连接
还通不通就行；授权列表在服务端每次增删时扇出即可。这条流常驻，顺带兼作心跳：
真断开了客户端自己会看到，``: ping`` 只是为了让中间代理别把闲置连接掐掉。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from nono_app_server.engine.deps import get_session_manager
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.permissions import service as permissions

router = APIRouter()

_PING_S = 15.0


def _frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/control/stream")
async def control_stream(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
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
                if await request.is_disconnected():
                    return
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

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
