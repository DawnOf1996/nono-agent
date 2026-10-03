"""终端功能的部署期配置。

全部走环境变量，与 ``nono_app_server.config`` 的 sidecar 设置同一套读法。终端
的数量与存活时间是部署选择而不是协议常量，所以做成配置项而非模块常量：终端
在服务端常驻，每个都占一个 PTY 进程、一份模拟器屏幕和若干条客户端队列。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_MAX_PER_WORKSPACE = "NONO_TERMINAL_MAX_PER_WORKSPACE"
ENV_MAX_TOTAL = "NONO_TERMINAL_MAX_TOTAL"
ENV_SCROLLBACK = "NONO_TERMINAL_SCROLLBACK"
ENV_MAX_COLS = "NONO_TERMINAL_MAX_COLS"
ENV_MAX_ROWS = "NONO_TERMINAL_MAX_ROWS"
ENV_MAX_QUEUE = "NONO_TERMINAL_MAX_QUEUE"
ENV_IDLE_TTL = "NONO_TERMINAL_IDLE_TTL_S"
ENV_EXIT_TTL = "NONO_TERMINAL_EXIT_TTL_S"
ENV_REAP_INTERVAL = "NONO_TERMINAL_REAP_INTERVAL_S"


def _int(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class TerminalSettings:
    """一个 sidecar 进程内所有终端会话共享的上限与存活策略。"""

    #: 单个工作区同时存活的终端上限。
    max_per_workspace: int
    #: 整个 sidecar 的终端上限，跨工作区生效。
    max_total: int
    #: 模拟器保留的历史行数；只影响服务端内存，不改变重连后的视口恢复。
    scrollback: int
    max_cols: int
    max_rows: int
    #: 单条客户端队列的待发字节数上限，超过即要求它重新取快照。
    max_queue_bytes: int
    #: 无客户端且无输出的秒数上限，超过后回收进程。0 表示不回收。
    idle_ttl_s: int
    #: 会话退出后保留的秒数，供客户端看到退出码；0 表示立即回收。
    exit_ttl_s: int
    #: 回收扫描间隔。
    reap_interval_s: int


def load_terminal_settings() -> TerminalSettings:
    """从环境变量读取终端配置，缺省值面向桌面单用户场景。"""
    return TerminalSettings(
        max_per_workspace=_int(ENV_MAX_PER_WORKSPACE, 8, 1),
        max_total=_int(ENV_MAX_TOTAL, 24, 1),
        scrollback=_int(ENV_SCROLLBACK, 2000, 0),
        max_cols=_int(ENV_MAX_COLS, 500, 2),
        max_rows=_int(ENV_MAX_ROWS, 200, 1),
        max_queue_bytes=_int(ENV_MAX_QUEUE, 2 * 1024 * 1024, 4096),
        idle_ttl_s=_int(ENV_IDLE_TTL, 1800, 0),
        exit_ttl_s=_int(ENV_EXIT_TTL, 600, 0),
        reap_interval_s=_int(ENV_REAP_INTERVAL, 60, 5),
    )


__all__ = ["TerminalSettings", "load_terminal_settings"]
