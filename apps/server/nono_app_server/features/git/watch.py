"""工作树「可能变了」的旁路信号。

服务端自己就知道改动发生过：一轮对话结束（``WorkspaceChangesService``）、
暂存/取消暂存/丢弃/提交（git 路由）。这些地方通知一下订阅者，订阅者立刻
重算状态并推给浏览器，而不是等下一次定时检查。

这只是「去重看一眼」的提示，不是权威来源：用户自己在终端里跑 git、
外部进程改文件，这些服务端不知道，仍靠 :mod:`~nono_app_server.features.git.api`
里那条流的签名兜底。信号因此不携带路径，订阅者自己判断要不要理会。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_listeners: set[Callable[[], None]] = set()


def subscribe(listener: Callable[[], None]) -> Callable[[], None]:
    """订阅「工作树可能变了」。返回退订函数。"""
    _listeners.add(listener)

    def unsubscribe() -> None:
        _listeners.discard(listener)

    return unsubscribe


def notify_changed() -> None:
    """告诉所有订阅者去重算一遍。没订阅者时是空操作。"""
    for listener in list(_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001 - 一个坏订阅者不能影响 git 操作
            logger.warning("Git change listener failed", exc_info=True)
