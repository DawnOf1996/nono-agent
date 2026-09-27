"""把子代理里的命令归到父会话的任务板上。"""

from __future__ import annotations

from contextvars import ContextVar

jobs_session: ContextVar[str] = ContextVar("nono_jobs_session", default="")
jobs_parent: ContextVar[str] = ContextVar("nono_jobs_parent", default="")
