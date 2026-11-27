"""会话级作业注册表。

取代 ``jobs/board.py`` 的模块级全局 + ``threading.Timer``：
- **实例化** —— 每个会话一份，作业与游标不再跨会话串扰
- **事件循环原生** —— 实时推送用 asyncio task，取代跑在事件循环里的同步定时器
- **增量输出** —— 复用 :class:`~nono_agent.shell.spill.SpillWriter`，
  ``job_output(from_offset)`` 拿到的是增量而非每次全量重解码

与 DSH 的 ``dsh-jobs`` 同构：注册表拥有 id、归属、轮询与通知；执行器只管进程。

**abort 监听的所有权在这里。** 工具的前台调用在超时后会「晋升」为后台作业并立即
返回 —— 此时 ``executor`` 的 task 已经结束，不再有人负责响应用户中断。注册表接手
监听，用户中断时才能杀掉已经在后台的进程。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nono_agent.shell.spill import OutputDelta

logger = logging.getLogger(__name__)

# 实时推送的合批窗口（秒）。与旧 board 的 _FLUSH_S 对齐。
FLUSH_S = 0.08
# 单个作业保留的输出尾部上限（字符）。任务板预览够用；模型走 spill 全文。
MAX_OUTPUT_CHARS = 32_000
# 每个会话保留的已结束作业数，超出按开始时间淘汰最旧的。
MAX_FINISHED = 40

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"

# 订阅者回调：(该会话的作业快照列表) -> None
Listener = Callable[[list[dict[str, Any]]], None]


class _KillOnlyProcess:
    """只有终止能力、没有输出流的句柄。

    兼容旧的「先登记作业、拿到 kill 回调」调用模式；新代码直接传真的进程句柄。
    """

    def __init__(self, kill: Callable[[], None]) -> None:
        self._kill = kill

    def kill(self) -> None:
        self._kill()


@dataclass
class Job:
    """一个已登记的作业。字段与旧 ``board.Job.public()`` 对齐（SSE 帧依赖）。"""

    id: str
    session_id: str
    kind: str
    title: str
    status: str = STATUS_RUNNING
    exit_code: int | None = None
    parent_id: str | None = None
    started_at: float = field(default_factory=time.time)
    process: Any = None
    abort_watch: asyncio.Task[None] | None = None
    # 模型侧已读到的 stdout 游标（由 job_output 工具推进）
    cursor: int = 0
    # 显式设置的预览文本。None 表示预览实时取自进程流（正常路径）；
    # 旧调用点用整段文本覆盖时走这里（兼容路径）。
    preview_override: str | None = None

    def public(self) -> dict[str, Any]:
        """对外快照。字段名是 SSE 协议的一部分，不要随意改。"""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "output": self.preview(),
            "exit_code": self.exit_code,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
        }

    def preview(self, limit: int = MAX_OUTPUT_CHARS) -> str:
        """任务板预览文本（只给 UI，不是模型的输出通道）。"""
        if self.preview_override is not None:
            return self.preview_override[-limit:]
        streams = getattr(self.process, "streams", None)
        if streams is None:
            return ""
        stdout, stderr = streams.deltas()
        text: str = stdout.text + stderr.text
        return text[-limit:]


class JobRegistry:
    """一个会话的作业注册表。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._jobs: dict[str, Job] = {}
        self._listeners: list[Listener] = []
        self._dirty = False
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 登记与终止
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        kind: str,
        title: str,
        process: Any = None,
        parent_id: str | None = None,
    ) -> Job:
        """登记一个作业。

        ``process`` 允许为 None：工具必须**先登记再 spawn**（否则极短命的命令
        在登记前就结束，UI 看不到），拿到句柄后用 :meth:`adopt` 补挂。
        """
        job = Job(
            id=f"job-{uuid.uuid4().hex[:12]}",
            session_id=self.session_id,
            kind=kind,
            title=title.strip() or kind,
            parent_id=parent_id,
            process=process,
        )
        self._jobs[job.id] = job
        self._mark()
        return job

    def adopt(self, job_id: str, process: Any) -> None:
        """把已启动的进程挂到先登记的作业上。

        工具需要**先登记再 spawn**（否则极短命的命令在登记前就结束，UI 看不到），
        但终止能力要等句柄到手才有。两步之间用本方法衔接。
        """
        job = self._jobs.get(job_id)
        if job is not None:
            job.process = process
            self._mark()

    def adopt_kill(self, job_id: str, kill: Callable[[], None]) -> None:
        """给已登记的作业补挂一个同步终止回调（兼容旧的 kill 回调式调用点）。"""
        job = self._jobs.get(job_id)
        if job is not None:
            job.process = _KillOnlyProcess(kill)
            self._mark()

    def set_preview(self, job_id: str, text: str) -> None:
        """整段覆盖预览文本（兼容旧的 ``set_output`` 调用点）。"""
        job = self._jobs.get(job_id)
        if job is not None:
            job.preview_override = text
            self._mark()

    def mark_dirty(self) -> None:
        """通知订阅者「有变化」（不改变作业内容）。"""
        self._mark()

    async def finish(
        self, job_id: str, *, status: str, exit_code: int | None = None
    ) -> None:
        """标记作业结束并取消它的 abort 监听（任务已终结，再监听无意义）。"""
        job = self._jobs.get(job_id)
        if job is None or job.status != STATUS_RUNNING:
            return
        job.status = status
        job.exit_code = exit_code
        if job.abort_watch is not None:
            job.abort_watch.cancel()
            job.abort_watch = None
        self._mark()
        self._trim()

    def attach_abort_watch(self, job_id: str, signal: Any) -> None:
        """把 abort 监听的所有权移交给注册表。

        这是「超时晋升」能成立的关键：工具的 ``execute`` 已经返回，executor 的
        task 已结束，若无人继续监听，用户中断就杀不掉后台进程。
        """
        job = self._jobs.get(job_id)
        if job is None or signal is None:
            return
        if job.process is not None:
            job.abort_watch = job.process.watch_abort(signal)
            return

        async def _watch() -> None:
            await signal.wait()
            await self.kill(job_id, "aborted by user")

        job.abort_watch = asyncio.create_task(_watch())

    def detach_abort_watch(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None and job.abort_watch is not None:
            job.abort_watch.cancel()
            job.abort_watch = None

    async def kill(self, job_id: str, reason: str = "model request") -> bool:
        """终止作业。幂等；重复调用返回 False 而不报错。"""
        job = self._jobs.get(job_id)
        if job is None or job.status != STATUS_RUNNING:
            return False
        job.status = STATUS_KILLED
        if job.abort_watch is not None:
            job.abort_watch.cancel()
            job.abort_watch = None
        if job.process is not None:
            await job.process.kill()
        self._mark()
        return True

    async def kill_all(self) -> None:
        """收尾：杀掉全部在跑的作业。

        引擎/会话销毁时必须调用，否则晋升到后台的进程会变成孤儿。
        """
        running = [j.id for j in self._jobs.values() if j.status == STATUS_RUNNING]
        for job_id in running:
            await self.kill(job_id, "owner disposed")
        for job in self._jobs.values():
            if job.abort_watch is not None:
                job.abort_watch.cancel()
                job.abort_watch = None

    def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        self._mark()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_ids(self) -> list[Job]:
        """本会话的全部作业（按开始时间排序）。给工具列「有哪些 job 可用」。"""
        return sorted(self._jobs.values(), key=lambda j: j.started_at)

    def list(self) -> list[dict[str, Any]]:
        """按开始时间排序的作业快照（SSE 帧数据源）。"""
        return sorted(
            (j.public() for j in self._jobs.values()), key=lambda r: r["started_at"]
        )

    def read(self, job_id: str, from_offset: int | None = None) -> OutputDelta | None:
        """从游标读增量输出，并把作业游标推进到读到的位置。

        ``from_offset`` 省略时从作业自己的游标继续（模型轮询语义）；
        显式给出时从该偏移读（不推进作业游标）。
        """
        job = self._jobs.get(job_id)
        if job is None or job.process is None:
            return None
        offset: int = job.cursor if from_offset is None else from_offset
        delta: OutputDelta = job.process.streams.stdout.read_from(offset)
        if from_offset is None:
            job.cursor = delta.next_offset
        return delta

    def stderr_delta(self, job_id: str) -> OutputDelta | None:
        job = self._jobs.get(job_id)
        if job is None or job.process is None:
            return None
        delta: OutputDelta = job.process.streams.stderr.read_from(0)
        return delta

    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == STATUS_RUNNING)

    # ------------------------------------------------------------------
    # 订阅（SSE 消费路径，不能断）
    # ------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """订阅作业变化。返回取消订阅函数。"""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _mark(self) -> None:
        """有订阅者时合批推送。输出是一块块来的，不逐块推。

        没有运行中的事件循环时（同步调用方，如库用法直接调工具）退化为立即
        同步推送 —— 旧实现用 ``threading.Timer``，在无 loop 环境也能工作。
        """
        if not self._listeners:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.flush_now()
            return
        if self._flush_task is not None:
            return
        self._dirty = True
        self._flush_task = asyncio.create_task(self._flush_after())

    async def _flush_after(self) -> None:
        await asyncio.sleep(FLUSH_S)
        self._flush_task = None
        if not self._dirty:
            return
        self._dirty = False
        rows = self.list()
        for listener in list(self._listeners):
            try:
                listener(rows)
            except Exception:  # noqa: BLE001 —— 一个坏订阅者不应影响其他
                logger.debug("job listener failed", exc_info=True)

    def flush_now(self) -> None:
        """立即把当前快照推给订阅者（测试与「立即要结果」的路径用）。"""
        rows = self.list()
        for listener in list(self._listeners):
            try:
                listener(rows)
            except Exception:  # noqa: BLE001
                logger.debug("job listener failed", exc_info=True)

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.status != STATUS_RUNNING]
        overflow = len(finished) - MAX_FINISHED
        if overflow <= 0:
            return
        finished.sort(key=lambda j: j.started_at)
        for job in finished[:overflow]:
            self._jobs.pop(job.id, None)


__all__ = [
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_KILLED",
    "STATUS_RUNNING",
    "Job",
    "JobRegistry",
]
