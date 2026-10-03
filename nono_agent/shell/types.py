"""Shell 执行能力协议。

工具不直接 spawn 进程：它描述一次执行（:class:`ShellSpec`），由执行器完成。
这条缝的价值在于**扩展点**：沙箱执行器继承 :class:`~nono_agent.shell.local.LocalShellExecutor`
复用全部进程机制，只覆写 argv 构造或包裹参数，不在工具里写 ``if sandbox_mode:`` 分支。

语义契约（实现方必须遵守）：

- :meth:`ShellProcess.wait` **只对基础设施故障 raise**。非零退出、超时、abort
  都 resolve 成 :class:`ShellOutcome`，由调用方决定如何渲染。
- :attr:`ShellProcess.streams` 是**非消费式**的：执行进行中可反复读取同一偏移，
  不会因为本次读而丢数据。作业注册表靠这个特性做实时推送与增量查询。
- ``on_expiry`` 二分：``"kill"`` 在执行器内部设 deadline；``"none"`` **完全不设**，
  由调用方自己限时等待 —— 句柄在调用方停止等待后依然有效。这是「超时晋升为后台」
  的前提。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nono_agent.shell.dialect import ShellDialect
from nono_agent.shell.spill import OutputDelta
from nono_agent.shell.streams import StreamPair

# on_expiry 取值
ON_EXPIRY_KILL = "kill"
ON_EXPIRY_NONE = "none"


@dataclass(frozen=True)
class ShellSpec:
    """一次执行的完整规格。调用方所有，对执行器不透明。"""

    command: str
    dialect: ShellDialect
    workdir: Path | None = None
    timeout_s: float = 120.0
    on_expiry: str = ON_EXPIRY_KILL
    env: dict[str, str] = field(default_factory=dict)
    stdin: bytes | None = None
    # 落盘目录；None 表示不落盘（超限即丢弃）
    spill_dir: Path | None = None
    max_output_bytes: int = 30_000
    max_spill_bytes: int = 64 * 1024 * 1024
    # stderr 是否并入 stdout（默认分离 —— 合并后模型分不清输出与诊断）
    merge_streams: bool = False


@dataclass(frozen=True)
class ShellOutcome:
    """前台执行的终态。"""

    exit_code: int | None
    signal: str | None
    timed_out: bool
    aborted: bool
    timeout_s: float
    stdout: OutputDelta
    stderr: OutputDelta
    # 显式终止（作业板停止按钮 / job_kill）。与 aborted（用户中断）不同源，
    # 但对渲染而言同样不能按 exit_code 推断成败 —— 被杀的进程可能带着 0 或 1。
    killed: bool = False
    streams: StreamPair | None = None
    # 沙箱执行器回填的事实（mode / denied / enforcement）。本地执行器为 None。
    sandbox: dict[str, Any] | None = None

    @property
    def status(self) -> str:
        if self.timed_out:
            return "timed_out"
        if self.aborted or self.killed or self.signal is not None:
            return "killed"
        return "completed" if self.exit_code == 0 else "failed"


@runtime_checkable
class ShellProcess(Protocol):
    """活着的进程句柄。"""

    @property
    def status(self) -> str:
        """``running`` / ``completed`` / ``failed`` / ``killed``。"""
        ...

    @property
    def streams(self) -> StreamPair:
        """非消费式的输出缓冲，执行中可反复读。"""
        ...

    async def wait(self) -> ShellOutcome:
        """等到进程结束（或 ``on_expiry`` 触发）。"""
        ...

    async def kill(self) -> None:
        """终止进程树并有界等待收尾。幂等，不抛异常。"""
        ...


@runtime_checkable
class ShellExecutor(Protocol):
    """把 :class:`ShellSpec` 变成一个 :class:`ShellProcess`。"""

    @property
    def sandbox_mode(self) -> str | None:
        """本执行器默认施加的沙箱模式；不沙箱时为 None。"""
        ...

    async def execute(
        self,
        spec: ShellSpec,
        signal: Any = None,
        *,
        on_output: Callable[[], None] | None = None,
    ) -> ShellProcess:
        """启动进程。**准备阶段超时**也要返回一个已结算的句柄，而不是抛错。"""
        ...


__all__ = [
    "ON_EXPIRY_KILL",
    "ON_EXPIRY_NONE",
    "ShellExecutor",
    "ShellOutcome",
    "ShellProcess",
    "ShellSpec",
]
