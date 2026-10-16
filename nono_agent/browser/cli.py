"""agent-browser CLI 的进程封装。

**这一层唯一的技术难点是：不能直接 ``capture_output=True``。**

agent-browser 每次调用会（或复用）一个后台 daemon，daemon 继承父进程的
stdout/stderr 句柄。只要用管道，子进程虽然早就把结果打印完了，父进程仍在等
「管道被所有写端关闭」——实测表现为 ``subprocess.run`` 永远不返回。绕开的办法
有两个，这里两个都用：

- 输出重定向到**临时文件**，不经过管道；
- 给每次调用一个硬超时，超时连同子孙进程一起杀。

实测（对着本机 Electron 的 baidu webview）::

    tab list --json          103 ms
    tab <targetId>           226 ms
    snapshot -i              644 ms
    fill @e28 你好            105 ms

这个量级对模型可接受，所以**不用**把 daemon 生命周期管理接进来；daemon 默认
空闲一小时后自己退出（``AGENT_BROWSER_IDLE_TIMEOUT_MS``）。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nono_agent.browser import cdp

# 单条命令的上限。实测最慢的 snapshot -i 也就 644ms；给到 30s 纯粹是防住
# 「页面卡死时永远挂着」——挂着的工具调用会让整个会话停在那儿。
_DEFAULT_TIMEOUT_S = 30.0

# Windows 上杀进程树要带这个，否则 daemon 会活下来继续占着端口。
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class AgentBrowserError(RuntimeError):
    """agent-browser 调用失败 —— 消息直接给模型看，要说人话。"""


@dataclass(frozen=True)
class AgentBrowserResult:
    stdout: str
    stderr: str
    exit_code: int


def binary() -> str:
    """可执行文件路径；主进程没注入就抛。

    错误消息里带上「去哪儿放」——这是个需要人动手补齐的依赖，模型自己猜不出来，
    也不该让它猜。
    """
    path = cdp.binary_path()
    if not path:
        raise AgentBrowserError(
            "This build has no bundled agent-browser executable. Put "
            "agent-browser-win32-x64.exe into apps/desktop/resources/agent-browser/ "
            "and rebuild; in development you can also point at it with the "
            "NONO_BROWSER_BIN environment variable."
        )
    if not Path(path).is_file():
        raise AgentBrowserError(f"The agent-browser executable does not exist: {path}")
    return path


def base_args() -> list[str]:
    """所有命令共用的前置参数。

    ``--pin-tab`` 是**必需品**而不是保险：实测只要停靠了 DevTools，daemon 的
    active target 就会被抢走，snapshot 会打到 DevTools 自己的界面上，返回一堆
    无关的节点。钉死之后，目标标签被关掉时报 ``tab_gone`` 而不是悄悄落到别的
    标签上去 —— 后者会让模型对着一个它没选过的页面操作。
    """
    return ["--cdp", str(cdp.port()), "--pin-tab"]


def run(args: list[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> AgentBrowserResult:
    """同步执行一条命令。"""
    return asyncio.run(run_async(args, timeout_s=timeout_s))


async def run_async(
    args: list[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S
) -> AgentBrowserResult:
    """执行一条命令并把 ``AgentBrowserResult`` 原样交回去（多数命令是纯文本）。

    输出落临时文件而不是管道：``asyncio.create_subprocess_exec`` 只能给 PIPE，
    而管道会被 daemon 一直吊着。所以整条路径走线程池里的同步 spawn。
    """
    argv = [binary(), *base_args(), *args]
    with tempfile.TemporaryDirectory(prefix="nono-ab-") as tmp:
        out_path = Path(tmp) / "out.txt"
        err_path = Path(tmp) / "err.txt"
        return await asyncio.to_thread(
            _spawn_and_wait, argv, out_path, err_path, timeout_s
        )


def _spawn_and_wait(
    argv: list[str], out_path: Path, err_path: Path, timeout_s: float
) -> AgentBrowserResult:
    with out_path.open("wb") as out, err_path.open("wb") as err:
        proc = subprocess.Popen(  # noqa: S603 - argv 是固定前缀 + 内部拼装的参数
            argv,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            env=_child_env(),
            **_no_window(),
        )
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise AgentBrowserError(
                f"agent-browser {' '.join(argv[1:])} did not return within {timeout_s:g}s and "
                f"was aborted. The page may be hung -- try reloading it."
            ) from None

    return AgentBrowserResult(
        stdout=_read(out_path),
        stderr=_read(err_path),
        exit_code=proc.returncode,
    )


def run_json(args: list[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """跑一条命令并解析它的 ``--json`` 输出。"""
    result = asyncio.run(run_json_async(args, timeout_s=timeout_s))
    payload = _parse_json(result.stdout) if result.stdout.strip() else {}
    if payload.get("success") is False:
        raise AgentBrowserError(
            _describe_error(payload) or result.stderr.strip() or "command failed"
        )
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


async def run_json_async(
    args: list[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """``--json`` 版的异步接口。"""
    result = await run_async(["--json", *args], timeout_s=timeout_s)
    payload = _parse_json(result.stdout) if result.stdout.strip() else {}
    if payload.get("success") is False:
        raise AgentBrowserError(
            _describe_error(payload) or result.stderr.strip() or "command failed"
        )
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 进程细节
# ---------------------------------------------------------------------------


def _no_window() -> dict[str, Any]:
    """不要弹出黑色控制台窗口。

    sidecar 是被桌面壳 spawn 的子进程，带 console 会闪一个黑框；桌面应用里
    凭空多一个控制台窗口比报错还难受。仓库惯例统一收在
    ``utils.process`` 里，这里是本模块自带的最小版本。
    """
    flags: dict[str, Any] = {}
    if os.name == "nt":
        flags["creationflags"] = _CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
    return flags


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    # 输出不是终端，别让 CLI 往 stderr 塞一堆 ANSI 进度条。
    env["NO_COLOR"] = "1"
    return env


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """连 daemon 一起杀。

    只 ``kill()`` 掉直接子进程的话，daemon 会继续活着持有 CDP 会话，下一次
    ``tab <targetId>`` 仍然作用在同一个绑定上 —— 表现为「模型点了 A 页，界面
    上的 B 页动了」。
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            check=False,
            capture_output=True,
            **_no_window(),
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - taskkill 也没杀掉
        pass


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        # CLI 有时会在 JSON 前面打一行进度提示。截取第一个 { 到最后一个 }。
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AgentBrowserError(
                f"Could not parse agent-browser output: {text[:200]!r}"
            ) from None
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            raise AgentBrowserError(
                f"Could not parse agent-browser output: {text[:200]!r}"
            ) from None
    return parsed if isinstance(parsed, dict) else {}


def _describe_error(payload: dict[str, Any]) -> str:
    """把 CLI 的错误结构翻成一句人话。"""
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        message = str(error.get("message") or "").strip()
        if code == "tab_gone":
            return (
                "The bound tab has been closed. Run browser_tabs to see which tabs "
                "are open now, then browser_snapshot to pick one again."
            )
        return message or code or "command failed"
    if isinstance(error, str):
        return error
    return ""
