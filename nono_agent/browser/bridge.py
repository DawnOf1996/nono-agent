"""Agent 与桌面端之间的请求/回执总线。

**为什么需要它**：内置浏览器活在 Electron 渲染进程里（一个 ``<webview>`` 元素），
而工具跑在本进程的引擎里。两者之间没有任何现成通路 —— ``nono_agent`` 不依赖
``nono_app_server``，渲染进程也没有反向调用的通道。这里补一条：

    工具（引擎）──request()──▶ 队列 ──SSE──▶ 渲染进程
       ▲                                        │ 执行 openBrowser
       └────────── resolve() ◀──POST 回执───────┘

之所以把总线放在 ``nono_agent`` 而不是 sidecar：依赖方向是单向的
（sidecar 依赖引擎），反过来会让引擎反向依赖上层。sidecar 只做传输，
状态一律归这里 —— 与 :mod:`nono_app_server.features.permissions.service`
把待办队列放在服务层、让 HTTP 层只负责扇出是同一个道理。

**连接即能力**：渲染进程一旦连上 SSE，就说明桌面端在场。web 版永远不会连，
于是工具能在几百毫秒内就失败，而不是干等超时。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 没有桌面端在场时立刻返回，不排队、不等待。
#
# 这是个同步判断（有没有 SSE 连接摆在那儿），所以是 0ms 而不是某个超时值 ——
# 模型看到明确的提示会改口说别的，不会对着一个永远不存在的端点反复重试。
# web 版永远不连这条流，于是 browser_* 工具在那边是廉价的失败。

# 桌面端在场时的默认上限。开着标签页要等 webview 挂载 + 拿到 dom-ready，
# 慢机器上几秒不算少，但仍要有上限，否则一次卡死会把整个会话拖住。
DEFAULT_TIMEOUT = 15.0

# 待发送的命令。渲染进程连上时会被抽干。
_queue: list[dict[str, Any]] = []
# request_id -> 回执。工具 await 的是这里的 future。
_pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
# 队列一变就通知（通常是 SSE 生成器），由它自己去读最新值。
_listeners: set[Callable[[], None]] = set()
# 有多少条渲染进程连接。非零即「桌面端在场」。
_attached = 0

# 保留最近若干条已完成请求的描述，只为让断线后的排查有据可查；
# 不存回执内容 —— 那可能含页面数据。
_recent: list[dict[str, Any]] = []
_RECENT_MAX = 20


def subscribe(listener: Callable[[], None]) -> Callable[[], None]:
    """订阅命令队列变化。返回退订函数。"""
    _listeners.add(listener)

    def unsubscribe() -> None:
        _listeners.discard(listener)

    return unsubscribe


def _notify() -> None:
    """通知订阅者「队列变了」，由它们各自去读最新值。"""
    for listener in list(_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001 - 一个坏订阅者不能拖垮别的请求
            logger.warning("Browser bridge listener failed", exc_info=True)


def is_attached() -> bool:
    """桌面端是否在场（至少有一条渲染进程连接）。"""
    return _attached > 0


def attach() -> Callable[[], None]:
    """登记一条渲染进程连接，返回断连时的清理函数。"""
    global _attached
    _attached += 1

    def detach() -> None:
        global _attached
        _attached = max(0, _attached - 1)

    return detach


def take_pending() -> list[dict[str, Any]]:
    """抽干待发送命令。每条带一个递增的 ``seq``，渲染进程据此去重。"""
    if not _queue:
        return []
    items = [
        {**item, "seq": index}
        for index, item in enumerate(_queue)
    ]
    _queue.clear()
    return items


async def request(
    payload: dict[str, Any],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """排一条命令给桌面端并等回执。

    没有桌面端连接时**立刻**返回 ``{"ok": False, "error": "no_desktop"}``：
    这个分支必须快，否则 web 版上的模型会对着一个永远不存在的端点反复重试。

    Args:
        payload: 命令体，例如 ``{"op": "open", "url": "..."}``。
        timeout: 等待回执的上限；``None`` 时桌面端缺席用短超时、在场用默认超时。

    Returns:
        渲染进程回执的字典。未连通时带 ``ok=False`` 与 ``error``。
    """
    if not is_attached():
        return {
            "ok": False,
            "error": "no_desktop",
            "message": "内置浏览器只在桌面端可用；当前没有桌面端连接。",
        }

    request_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    _pending[request_id] = future
    _queue.append({"request_id": request_id, **payload})
    _remember(request_id, payload.get("op", "?"))
    _notify()

    wait = timeout if timeout is not None else DEFAULT_TIMEOUT
    try:
        return await asyncio.wait_for(future, timeout=wait)
    except TimeoutError:
        logger.warning("Browser request timed out: %s (%s)", payload.get("op"), request_id)
        return {
            "ok": False,
            "error": "timeout",
            "message": f"桌面端 {wait:.0f}s 内没有回执。",
        }
    finally:
        _pending.pop(request_id, None)


def resolve(request_id: str, payload: dict[str, Any]) -> bool:
    """投递一条回执。不存在、已完成或已超时都返回 False。"""
    future = _pending.get(request_id)
    if future is None or future.done():
        return False
    future.set_result(payload)
    return True


def stats() -> dict[str, Any]:
    """给排障看的快照。不含页面数据。"""
    return {
        "attached": _attached,
        "queued": len(_queue),
        "pending": len(_pending),
        "recent": list(_recent),
    }


def _remember(request_id: str, op: str) -> None:
    """记一条最近发生过什么。只留操作名与时间，不留 url 之外的任何内容。"""
    _recent.append({"request_id": request_id, "op": op, "at": time.time()})
    if len(_recent) > _RECENT_MAX:
        del _recent[: len(_recent) - _RECENT_MAX]


def cancel_all(reason: str = "shutting down") -> int:
    """让所有挂起的请求立刻失败。渲染进程断连或应用退出时调用。"""
    count = 0
    for future in list(_pending.values()):
        if not future.done():
            future.set_result(
                {"ok": False, "error": "cancelled", "message": reason}
            )
            count += 1
    if count:
        logger.info("Cancelled %s browser request(s): %s", count, reason)
    return count
