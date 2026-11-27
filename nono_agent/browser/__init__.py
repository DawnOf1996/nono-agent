"""内置浏览器控制 —— 引擎侧的请求/回执总线。"""

from __future__ import annotations

from nono_agent.browser import bridge, cdp
from nono_agent.browser.bridge import (
    attach,
    cancel_all,
    is_attached,
    request,
    resolve,
    stats,
    subscribe,
    take_pending,
)

__all__ = [
    "attach",
    "bridge",
    "cancel_all",
    "cdp",
    "is_attached",
    "request",
    "resolve",
    "stats",
    "subscribe",
    "take_pending",
]
