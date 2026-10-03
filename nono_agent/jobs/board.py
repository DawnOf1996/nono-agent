"""任务板的兼容门面。

真正的状态在 :class:`nono_agent.shell.jobs.JobRegistry` 里，按会话实例化。
本模块保留旧的模块级函数签名，作为**按 ``jobs_session`` 上下文路由**的薄封装 ——
Web 端的任务板 SSE（``features/jobs/api.py``）和子代理的作业归属
（``jobs_session`` ContextVar）都依赖这些调用点，不必同时改。

与旧实现的差别：

- 状态按会话分片，不再是进程级共享的一份 ``_jobs``（消除跨会话串扰）
- 实时推送走 asyncio 定时器，不再把 ``threading.Timer`` 塞进事件循环
- 作业输出是增量的（复用 SpillWriter 游标），不再是每次全量重解码后截断

新代码请直接用 :class:`JobRegistry`；本门面仅供既有调用点过渡。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from nono_agent.jobs.context import jobs_session
from nono_agent.shell.jobs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_KILLED,
    STATUS_RUNNING,
    JobRegistry,
)

# 未绑定会话的作业（库用法直接调工具时）归到这里。
_ANONYMOUS = "anonymous"

# 注册表按会话分片。锁只保护「取/建注册表」这一步；作业状态本身在事件循环里
# 单线程串行改，不需要额外同步。
_registry_lock = threading.Lock()
_registries: dict[str, JobRegistry] = {}


def registry_for(session_id: str | None = None) -> JobRegistry:
    """取某个会话的注册表，没有就建一个。

    ``session_id`` 省略时用 ``jobs_session`` 上下文（子代理路径会设置它），
    再退到匿名分片。
    """
    key = session_id or jobs_session.get() or _ANONYMOUS
    with _registry_lock:
        registry = _registries.get(key)
        if registry is None:
            registry = JobRegistry(key)
            _registries[key] = registry
        return registry


def open_job(
    *,
    session_id: str,
    kind: str,
    title: str,
    parent_id: str | None = None,
    kill: Callable[[], None] | None = None,
) -> str:
    """登记一个正在运行的任务。没有会话 id 时不登记，返回空串。"""
    if not session_id:
        return ""
    registry = registry_for(session_id)
    job = registry.start(kind=kind, title=title, parent_id=parent_id)
    if kill is not None:
        registry.adopt_kill(job.id, kill)
    return job.id


def attach_process(session_id: str, job_id: str, process: Any) -> None:
    """把真实进程句柄挂到已登记的作业上。"""
    registry_for(session_id).adopt(job_id, process)


def attach_kill(job_id: str, kill: Callable[[], None]) -> None:
    """给已登记的作业补挂终止回调（任务板的停止按钮依赖它）。"""
    registry_for().adopt_kill(job_id, kill)


def set_output(job_id: str, text: str) -> None:
    """整段替换作业预览文本。

    兼容入口：新实现里预览实时取自进程流，只有旧调用点才需要整段覆盖。
    """
    for registry in _all_registries():
        if registry.get(job_id) is not None:
            registry.set_preview(job_id, text)
            return


def append_output(job_id: str, text: str) -> None:
    """兼容入口：输出由进程流提供，此处只触发一次推送。"""
    set_output(job_id, text)


def finish_job(job_id: str, *, status: str, exit_code: int | None = None) -> None:
    """同步标记作业结束（兼容旧签名）。

    新代码用 ``await JobRegistry.finish()``，它还会取消 abort 监听。
    这里的同步版本同样要清监听，否则作业已终结却还在等 abort。
    """
    for registry in _all_registries():
        job = registry.get(job_id)
        if job is None or job.status != STATUS_RUNNING:
            continue
        job.status = status
        job.exit_code = exit_code
        if job.abort_watch is not None:
            job.abort_watch.cancel()
            job.abort_watch = None
        registry.mark_dirty()
        return


def kill_job(session_id: str, job_id: str) -> bool:
    """终止作业（任务板停止按钮）。返回是否真的终止了一个在跑的作业。

    同步入口：作业状态立即置为 killed（调用方是 HTTP handler，不能 await），
    真正的进程树终止由事件循环随后执行。
    """
    registry = registry_for(session_id)
    job = registry.get(job_id)
    if job is None or job.status != STATUS_RUNNING:
        return False
    job.status = STATUS_KILLED
    if job.abort_watch is not None:
        job.abort_watch.cancel()
        job.abort_watch = None
    process = job.process
    if process is not None:
        _schedule_terminate(process)
    registry.mark_dirty()
    return True


def _schedule_terminate(process: Any) -> None:
    """把进程树终止排进事件循环。终止本身是 async（要等管道收尾）。"""
    kill = getattr(process, "kill", None)
    if kill is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        kill()  # 无事件循环时只能同步调用
        return
    result = kill()
    if asyncio.iscoroutine(result):
        loop.create_task(result)


def list_jobs(session_id: str) -> list[dict[str, Any]]:
    """按开始时间排序的作业快照。"""
    return registry_for(session_id).list()


def subscribe(
    session_id: str, listener: Callable[[list[dict[str, Any]]], None]
) -> Callable[[], None]:
    """订阅任务变化。返回取消订阅。没有订阅者时不会排队刷新。"""
    return registry_for(session_id).subscribe(listener)


def reset_jobs() -> None:
    """测试隔离用：丢弃全部分片。"""
    with _registry_lock:
        _registries.clear()


def _all_registries() -> list[JobRegistry]:
    with _registry_lock:
        return list(_registries.values())


__all__ = [
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_KILLED",
    "STATUS_RUNNING",
    "append_output",
    "attach_kill",
    "attach_process",
    "finish_job",
    "kill_job",
    "list_jobs",
    "open_job",
    "registry_for",
    "reset_jobs",
    "set_output",
    "subscribe",
]
