"""会话级任务板：正在跑的命令和子代理，带实时输出和强制停止。"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_MAX_OUTPUT = 32_000
_MAX_FINISHED = 40
_FLUSH_S = 0.08

_lock = threading.Lock()
_jobs: dict[str, Job] = {}
_listeners: dict[str, list[Callable[[list[dict[str, Any]]], None]]] = {}
_dirty: set[str] = set()
_flush_timer: threading.Timer | None = None


@dataclass
class Job:
    id: str
    session_id: str
    kind: str
    title: str
    status: str = "running"
    output: str = ""
    exit_code: int | None = None
    parent_id: str | None = None
    started_at: float = field(default_factory=time.time)
    _kill: Callable[[], None] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "output": self.output,
            "exit_code": self.exit_code,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
        }


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
    job = Job(
        id=uuid.uuid4().hex,
        session_id=session_id,
        kind=kind,
        title=title.strip() or kind,
        parent_id=parent_id or None,
        _kill=kill,
    )
    with _lock:
        _jobs[job.id] = job
        _trim(session_id)
    _mark(session_id)
    return job.id


def append_output(job_id: str, text: str) -> None:
    if not job_id or not text:
        return
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status != "running":
            return
        job.output = (job.output + text)[-_MAX_OUTPUT:]
        session_id = job.session_id
    _mark(session_id)


def finish_job(job_id: str, *, status: str, exit_code: int | None = None) -> None:
    if not job_id:
        return
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status == "killed":
            return
        job.status = status
        job.exit_code = exit_code
        session_id = job.session_id
    _mark(session_id)


def kill_job(session_id: str, job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.session_id != session_id or job.status != "running":
            return False
        job.status = "killed"
        kill = job._kill
    if kill is not None:
        try:
            kill()
        except Exception:
            _mark(session_id)
            return True
    _mark(session_id)
    return True


def list_jobs(session_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = [job.public() for job in _jobs.values() if job.session_id == session_id]
    rows.sort(key=lambda item: item["started_at"])
    return rows


def subscribe(
    session_id: str, listener: Callable[[list[dict[str, Any]]], None]
) -> Callable[[], None]:
    """会话任务变化时回调当前列表。返回取消订阅。没有订阅者时不会排队刷新。"""
    with _lock:
        _listeners.setdefault(session_id, []).append(listener)

    def unsubscribe() -> None:
        with _lock:
            items = _listeners.get(session_id)
            if not items:
                return
            try:
                items.remove(listener)
            except ValueError:
                return
            if not items:
                _listeners.pop(session_id, None)

    return unsubscribe


def reset_jobs() -> None:
    """测试隔离用。"""
    global _flush_timer
    with _lock:
        _jobs.clear()
        _dirty.clear()
        timer = _flush_timer
        _flush_timer = None
        _listeners.clear()
    if timer is not None:
        timer.cancel()


def _snapshot(session_id: str) -> list[dict[str, Any]]:
    rows = [job.public() for job in _jobs.values() if job.session_id == session_id]
    rows.sort(key=lambda item: item["started_at"])
    return rows


def _mark(session_id: str) -> None:
    """有订阅者时，把这次变化并进下一次刷新。输出是一块一块来的，不逐块推送。"""
    global _flush_timer
    if not session_id:
        return
    with _lock:
        if not _listeners.get(session_id):
            return
        _dirty.add(session_id)
        if _flush_timer is not None:
            return
        timer = threading.Timer(_FLUSH_S, _flush)
        timer.daemon = True
        _flush_timer = timer
    timer.start()


def _flush() -> None:
    global _flush_timer
    with _lock:
        _flush_timer = None
        sessions = list(_dirty)
        _dirty.clear()
        batch = [
            (list(_listeners.get(session_id, ())), _snapshot(session_id)) for session_id in sessions
        ]
    for listeners, rows in batch:
        for listener in listeners:
            try:
                listener(rows)
            except Exception:
                continue


def _trim(session_id: str) -> None:
    finished = [
        job for job in _jobs.values() if job.session_id == session_id and job.status != "running"
    ]
    overflow = len(finished) - _MAX_FINISHED
    if overflow <= 0:
        return
    finished.sort(key=lambda job: job.started_at)
    for job in finished[:overflow]:
        _jobs.pop(job.id, None)
