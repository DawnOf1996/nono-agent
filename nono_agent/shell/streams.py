"""stdout / stderr 分离收集。

原实现把 stderr 并进 stdout（``stderr=asyncio.subprocess.STDOUT``），两条流混在
一起，模型分不清「命令的正常输出」和「诊断信息」。本模块保持分离，渲染时再合成
带 ``[stderr]`` 标记的段落 —— 与 DSH 的 shell 工具渲染约定一致。

两条流各自持有一个 :class:`~nono_agent.shell.spill.SpillWriter`，所以它们有各自的
绝对字节偏移空间；``read_from`` 各自独立。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nono_agent.shell.spill import OutputDelta, SpillWriter

_READ_SIZE = 8192

# 收到新字节时回调，供作业注册表做实时推送（UI SSE）。参数是该流的 channel 名。
OnDelta = Callable[[str], None]


@dataclass
class StreamPair:
    """一次执行的两条输出流。

    ``merged=True`` 表示 stderr 被写进 stdout 的 writer（``ShellConfig.merge_streams``）。
    此时两条流是同一份数据，读取方必须只取一次 —— 否则输出会重复一遍。
    """

    stdout: SpillWriter
    stderr: SpillWriter
    merged: bool = False

    @property
    def spill_paths(self) -> list[Path]:
        if self.merged:
            return [self.stdout.spill_path] if self.stdout.spill_path else []
        return [p for p in (self.stdout.spill_path, self.stderr.spill_path) if p is not None]

    @property
    def total_bytes(self) -> int:
        return self.stdout.total_bytes if self.merged else (
            self.stdout.total_bytes + self.stderr.total_bytes
        )

    def deltas(self) -> tuple[OutputDelta, OutputDelta]:
        """(stdout, stderr) 两个增量。合并模式下 stderr 恒为空。"""
        out = self.stdout.read_from(0)
        return (out, _EMPTY) if self.merged else (out, self.stderr.read_from(0))


# 合并模式下代替 stderr 的空增量。
_EMPTY = OutputDelta(text="", next_offset=0, start_offset=0, lossy=False,
                     spill_path=None, truncated=False)


class StreamCollector:
    """把进程的两条管道持续读进 :class:`StreamPair`，直到 EOF。

    读取是「非消费式」的：作业注册表可以在执行进行中反复 ``read_from`` 同一个偏移，
    不会因为本次读而丢数据。
    """

    def __init__(self, streams: StreamPair) -> None:
        self._streams = streams

    async def pump(
        self,
        proc: asyncio.subprocess.Process,
        *,
        on_delta: OnDelta | None = None,
    ) -> None:
        """把两条管道读到 EOF。任一条出错都不影响另一条。"""
        await asyncio.gather(
            self._pump_one(proc.stdout, self._streams.stdout, "stdout", on_delta),
            self._pump_one(proc.stderr, self._streams.stderr, "stderr", on_delta),
        )

    async def _pump_one(
        self,
        reader: asyncio.StreamReader | None,
        writer: SpillWriter,
        channel: str,
        on_delta: OnDelta | None,
    ) -> None:
        if reader is None:
            return
        while True:
            block = await reader.read(_READ_SIZE)
            if not block:
                return
            writer.write(block)
            if on_delta is not None:
                on_delta(channel)


def render_streams(
    stdout: OutputDelta,
    stderr: OutputDelta,
    *,
    show_stderr_marker: bool = True,
) -> str:
    """把两条流合成模型看到的文本。

    - stdout 为空、stderr 也为空 → ``(no output)``（模型据此区分「静默成功」与
      「输出被吞掉」）
    - stderr 非空 → 追加 ``[stderr]`` 段落
    - 任一流截断 → 追加落盘提示，**放在最后**（模型上下文侧还有一层 20k token
      预算会保留头 500 / 尾 200 token，提示必须落在尾部才不会被二次截断吃掉）
    """
    parts: list[str] = []
    if stdout.text:
        parts.append(stdout.text)
    if stderr.text and stderr.text != stdout.text:
        # 合并模式下 stderr 与 stdout 是同一份数据，只呈现一次
        parts.append(f"[stderr]\n{stderr.text}" if show_stderr_marker else stderr.text)
    body = "\n".join(parts) if parts else "(no output)"

    notes: list[str] = []
    for delta in (stdout, stderr):
        if delta.spill_path is None:
            continue
        notes.append(f"[output truncated; full output: {delta.spill_path}]")
    if not notes:
        return body
    return f"{body}\n" + "\n".join(notes)


def compose_output(
    stdout: OutputDelta,
    stderr: OutputDelta,
    *,
    exit_code: int | None = None,
    signal: str | None = None,
    timed_out: bool = False,
    timeout_s: float | None = None,
    stopped: str | None = None,
) -> str:
    """前台执行的完整模型可见文本。

    **非零退出不是错误** —— 它以 ``[exit code: N]`` 标记出现，由模型自己判断是否
    要重试或换方案；只有 spawn 失败、abort 这类基础设施故障才作为工具错误上报。
    """
    body = render_streams(stdout, stderr)
    markers: list[str] = []
    if stopped is not None:
        markers.append(f"[stopped: {stopped}]")
    if timed_out and timeout_s is not None:
        markers.append(f"[timed out after {timeout_s:g}s]")
    if signal is not None:
        markers.append(f"[killed by signal: {signal}]")
    elif exit_code not in (None, 0):
        markers.append(f"[exit code: {exit_code}]")
    if not markers:
        return body
    return f"{body}\n" + "\n".join(markers)


__all__ = [
    "OnDelta",
    "StreamCollector",
    "StreamPair",
    "compose_output",
    "render_streams",
]
