"""本地执行器 —— 在本机 spawn 进程。

沙箱执行器（未来的 P2）继承本类、只覆写 :meth:`LocalShellExecutor.argv` 或包裹
参数，即可复用全部进程机制：超时分类、进程组终止、输出收集、游标语义。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nono_agent.shell.dialect import build_argv, env_overrides
from nono_agent.shell.proc import spawn_kwargs, terminate
from nono_agent.shell.spill import OutputDelta, SpillWriter
from nono_agent.shell.streams import StreamCollector, StreamPair
from nono_agent.shell.types import (
    ON_EXPIRY_KILL,
    ShellOutcome,
    ShellProcess,
    ShellSpec,
)

logger = logging.getLogger(__name__)

# 进程结束后等待输出管道读空的上界（秒）。管道被子进程持有时不能无限等。
DRAIN_GRACE_S = 3.0


class LocalShellExecutor:
    """在本地执行命令。本地不施加沙箱，故 ``sandbox_mode`` 为 None。"""

    sandbox_mode: str | None = None

    def __init__(self, *, base_env: dict[str, str] | None = None) -> None:
        self._base_env = dict(base_env or {})

    # ------------------------------------------------------------------
    # 扩展点
    # ------------------------------------------------------------------

    def argv(self, spec: ShellSpec) -> list[str]:
        """构造 argv。沙箱子类覆写这里以包裹可执行文件。"""
        return build_argv(spec.dialect, spec.command)

    def build_env(self, spec: ShellSpec) -> dict[str, str]:
        return {**os.environ, **self._base_env, **env_overrides(spec.env)}

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def execute(
        self,
        spec: ShellSpec,
        signal: Any = None,
        *,
        on_output: Callable[[], None] | None = None,
    ) -> ShellProcess:
        """启动进程。返回**立即可用**的句柄（不会等它结束）。

        ``on_output`` 在每块字节到达时被调用，作业注册表用它做实时推送
        （SSE 任务板）—— 事件循环原生，不需要轮询。
        """
        stdout_writer = SpillWriter(
            spec.max_output_bytes,
            _spill_file(spec, "stdout.log"),
            max_spill_bytes=spec.max_spill_bytes,
        )
        # 合并模式下 stderr 必须真的写进 stdout 的 writer —— 只把 StreamPair
        # 标成 merged 而不共用 writer，deltas() 会把 stderr 整段丢掉。
        stderr_writer = (
            stdout_writer
            if spec.merge_streams
            else SpillWriter(
                spec.max_output_bytes,
                _spill_file(spec, "stderr.log"),
                max_spill_bytes=spec.max_spill_bytes,
            )
        )
        streams = StreamPair(stdout=stdout_writer, stderr=stderr_writer, merged=spec.merge_streams)
        handle = LocalProcess(streams=streams, spec=spec)

        abort_task = handle.watch_abort(signal)

        try:
            proc = await asyncio.create_subprocess_exec(
                *self.argv(spec),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if spec.stdin is not None
                else asyncio.subprocess.DEVNULL,
                cwd=spec.workdir,
                env=self.build_env(spec),
                **spawn_kwargs(),
            )
        except (OSError, ValueError) as exc:
            handle.fail_to_start(str(exc))
            if abort_task is not None:
                abort_task.cancel()
            return handle

        handle.attach(proc, collector=StreamCollector(streams), on_output=on_output)
        return handle


def _spill_file(spec: ShellSpec, name: str) -> Path | None:
    return None if spec.spill_dir is None else spec.spill_dir / name


class LocalProcess:
    """一次本地执行。"""

    def __init__(self, *, streams: StreamPair, spec: ShellSpec) -> None:
        self._streams = streams
        self._spec = spec
        self._proc: asyncio.subprocess.Process | None = None
        self._collector: StreamCollector | None = None
        self._on_output: Callable[[], None] | None = None
        self._pump: asyncio.Task[None] | None = None
        self._status = "running"
        self._timed_out = False
        self._aborted = False
        self._killed = False
        self._start_error: str | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    @property
    def streams(self) -> StreamPair:
        return self._streams

    def attach(
        self,
        proc: asyncio.subprocess.Process,
        *,
        collector: StreamCollector,
        on_output: Callable[[], None] | None = None,
    ) -> None:
        self._proc = proc
        self._collector = collector
        self._on_output = on_output
        notify = on_output if on_output is not None else (lambda: None)
        self._pump = asyncio.create_task(collector.pump(proc, on_delta=lambda _: notify()))
        if spec_stdin := self._spec.stdin:
            asyncio.create_task(self._feed(proc, spec_stdin))

    async def _feed(self, proc: asyncio.subprocess.Process, data: bytes) -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 子进程提前退出是常态，不必上报

    def fail_to_start(self, error: str) -> None:
        """spawn 失败。``wait()`` 会以它 reject，句柄状态转 killed。"""
        self._start_error = error
        self._status = "killed"

    def watch_abort(self, signal: Any) -> asyncio.Task[None] | None:
        """监听 abort 信号并终止进程树。

        与「超时」是两条独立通路：超时由 ``on_expiry`` 的 deadline 负责，
        abort 走这里。二者不共享 kill 决策，因此用户中断与超时互不干扰。
        """
        if signal is None:
            return None

        async def _watch() -> None:
            await signal.wait()
            self._aborted = True
            await self.kill()

        return asyncio.create_task(_watch())

    async def wait(self) -> ShellOutcome:
        """等到进程结束。返回后句柄仍然可读（输出不丢）。"""
        async with self._lock:
            if self._start_error is not None:
                raise OSError(self._start_error)
            assert self._proc is not None, "process was never attached"

            if self._spec.on_expiry == ON_EXPIRY_KILL:
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=self._spec.timeout_s)
                except TimeoutError:
                    self._timed_out = True
                    await self.kill()
            else:
                # ON_EXPIRY_NONE：完全不设 deadline，由调用方限时等待
                await self._proc.wait()

            await self._drain()
            # 三种「非正常结束」都要压过 exit_code 推断：被杀、超时、被中断
            # 的进程可能带着 0 或 1 的退出码，不能因此显示成成功/失败。
            if self._timed_out or self._aborted or self._killed:
                self._status = "killed"
            else:
                self._status = "completed" if self._proc.returncode == 0 else "failed"

            stdout, stderr = self._streams.deltas()
            return ShellOutcome(
                exit_code=self._proc.returncode,
                signal=None,  # Windows 无 POSIX 信号语义；由 exit_code 表达
                timed_out=self._timed_out,
                aborted=self._aborted,
                timeout_s=self._spec.timeout_s,
                stdout=stdout,
                stderr=stderr,
                killed=self._killed,
                streams=self._streams,
                sandbox=None,
            )

    async def kill(self) -> None:
        """终止进程树并有界等待。幂等。"""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        self._killed = True
        await terminate(proc)
        await self._drain()
        self._status = "killed"

    async def _drain(self) -> None:
        """等输出收集读完剩余字节，但有界。"""
        if self._pump is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._pump), timeout=DRAIN_GRACE_S)
        except (TimeoutError, asyncio.CancelledError):
            self._pump.cancel()
        except Exception:  # noqa: BLE001 —— 收集失败不改写执行结果
            logger.debug("shell: output collector failed", exc_info=True)


__all__ = ["DRAIN_GRACE_S", "LocalProcess", "LocalShellExecutor", "OutputDelta"]
