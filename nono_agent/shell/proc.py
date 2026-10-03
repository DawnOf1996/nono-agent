"""子进程树的启动与终止。

**为什么需要这个模块**：执行器跑的是「shell → 命令」两层结构
（``powershell.exe -Command "python foo.py"``）。在 Windows 上 ``Process.kill()``
只终止直接子进程（shell 自己），它 spawn 的孙进程继续运行，**并且继续持有
stdout/stderr 管道**。于是 ``await proc.wait()`` 会一直阻塞到孙进程自然结束 ——
一个跑 10 分钟的命令即使「超时」也会让调用方再干等 10 分钟。

解决：把命令放进独立的进程组/会话，终止时杀整棵树；并且**无论如何都要给等待
设上界**，进程组没死也不阻塞返回。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from typing import Any

from nono_agent.utils.process import hidden_kwargs

logger = logging.getLogger(__name__)

# 发出终止信号后等待进程退出的上界（秒）。超过就放弃等待 —— 返回比僵住好。
KILL_GRACE_S = 3.0

# 独立进程组。POSIX 侧由 start_new_session 完成（setsid），Windows 侧用这个
# creationflag，让 taskkill /T 能定位到整棵树而不误伤父进程树。
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def spawn_kwargs() -> dict[str, Any]:
    """传给 ``create_subprocess_exec`` 的平台相关参数。

    在既有 ``hidden_kwargs()``（避免弹黑框）之上追加「独立进程组」，
    两者叠加而不是二选一。
    """
    kwargs: dict[str, Any] = dict(hidden_kwargs())
    if sys.platform == "win32":
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _kill_tree_blocking(pid: int) -> None:
    """同步杀整棵进程树。不阻塞调用方（内部已用 communicate 收尾）。"""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=KILL_GRACE_S,
            **hidden_kwargs(),
        )
        return
    # POSIX：start_new_session 使子进程成为组长，故组 id == leader pid
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # 组已消失或无权操作时，回落到直接杀 leader
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


async def terminate(proc: asyncio.subprocess.Process) -> None:
    """终止进程树并**有界**地等待它收尾。

    永远不抛异常、也不无限等待：管道被子进程持有时 ``proc.wait()`` 可能迟迟不返回，
    调用方（超时路径、abort 路径）不能被它拖住。
    """
    pid = proc.pid
    if pid is not None:
        try:
            await asyncio.to_thread(_kill_tree_blocking, pid)
        except Exception:  # noqa: BLE001 —— 终止是尽力而为，不能因它让工具整体失败
            logger.debug("shell: killing process tree %s failed", pid, exc_info=True)

    try:
        await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_S)
    except (TimeoutError, ProcessLookupError):
        logger.debug("shell: process tree %s did not settle within grace", pid)


def is_alive(proc: asyncio.subprocess.Process) -> bool:
    return proc.returncode is None


__all__ = ["KILL_GRACE_S", "is_alive", "spawn_kwargs", "terminate"]
