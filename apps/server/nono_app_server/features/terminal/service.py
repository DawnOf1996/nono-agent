"""持久终端会话：按工作区归属、服务端持有屏幕状态、可跨工作区切换存活。

进程与屏幕都由这里拥有。字节流先进 :class:`~.screen.TerminalScreen` 维护 VT
状态，客户端重连时拿一帧完整屏幕而不是文本尾——这让切换工作区后回来看到的内容
与离开时逐格一致，不受输出量、ANSI 序列边界或尺寸变化影响。

线程纪律：只有 ``proc`` 的阻塞调用在读线程/工作线程发生；``_sessions`` 与
``TerminalSession`` 的状态变更只在事件循环线程进行。``attach`` 返回队列与
首帧是同一个同步块，中间没有 await，因此与并发到达的 ``push``/``finish``
不存在缝隙。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from nono_app_server.common.paths import (
    within_root as _within_root,
)
from nono_app_server.common.paths import (
    workspace_root as _workspace_root,
)
from nono_app_server.features.terminal.config import TerminalSettings, load_terminal_settings
from nono_app_server.features.terminal.screen import TerminalScreen

logger = logging.getLogger(__name__)

_SW_HIDE = 0

_sessions: dict[str, TerminalSession] = {}

#: 读线程/工作线程的 spawn 串行锁；ConPTY 首次初始化不可重入。
_spawn_lock = threading.Lock()

_settings: TerminalSettings = load_terminal_settings()


class _Resync:
    """消费端落后太多时的队列标记：处理方据此补发一帧新快照。"""

    __slots__ = ()


RESYNC = _Resync()


@dataclass(slots=True)
class _Follower:
    """一条客户端附着：队列本身，加上入队时已知的积压字节数。"""

    queue: asyncio.Queue[bytes | None | _Resync]
    pending: int = 0
    #: 已脱节，等待处理方补发快照；脱节期间不再接收输出。
    desynced: bool = False


def settings() -> TerminalSettings:
    """当前生效的终端配置。测试可覆盖本模块的 ``_settings``。"""
    return _settings


@dataclass
class ShellSpec:
    id: str
    label: str
    argv: list[str]


def _git_bash_candidates() -> list[Path]:
    candidates: list[Path] = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe",
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
    git = shutil.which("git")
    if not git:
        return candidates
    # Git Bash 启动时 git 在 mingw64/bin，cmd 启动时在 cmd/。往上找到安装根目录的 bin/bash.exe。
    current = Path(git).resolve().parent
    for _ in range(4):
        candidates.append(current / "bin" / "bash.exe")
        candidates.append(current / "bash.exe")
        if current.parent == current:
            break
        current = current.parent
    return candidates


def _git_bash_exe() -> Path | None:
    found: list[Path] = []
    seen: set[str] = set()
    for path in _git_bash_candidates():
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            found.append(path)
    for path in found:
        if path.parent.name.lower() == "bin" and (path.parent.parent / "git-bash.exe").is_file():
            return path
    return found[0] if found else None


def _windows_shells() -> list[ShellSpec]:
    specs: list[ShellSpec] = []
    for shell_id, label, exe, extra in (
        ("powershell", "PowerShell", "powershell.exe", ["-NoLogo", "-NoExit"]),
        ("pwsh", "PowerShell 7", "pwsh.exe", ["-NoLogo", "-NoExit"]),
        ("cmd", "Command Prompt", "cmd.exe", ["/D", "/K", "chcp 65001>nul"]),
    ):
        if shutil.which(exe):
            specs.append(ShellSpec(shell_id, label, [exe, *extra]))
    bash = _git_bash_exe()
    if bash is not None:
        specs.append(ShellSpec("bash", "Git Bash", [str(bash), "--login", "-i"]))
    return specs


def _posix_shells() -> list[ShellSpec]:
    specs: list[ShellSpec] = []
    for shell_id, label, exe in (
        ("bash", "bash", "bash"),
        ("zsh", "zsh", "zsh"),
        ("sh", "sh", "sh"),
    ):
        found = shutil.which(exe)
        if found:
            specs.append(ShellSpec(shell_id, label, [found, "-i"]))
    return specs


def available_shells() -> list[ShellSpec]:
    return _windows_shells() if os.name == "nt" else _posix_shells()


def _shell(shell_id: str | None) -> ShellSpec:
    shells = available_shells()
    if not shells:
        raise HTTPException(status_code=400, detail="没有可用的 shell")
    if not shell_id:
        return shells[0]
    for spec in shells:
        if spec.id == shell_id:
            return spec
    raise HTTPException(status_code=400, detail="不支持这个 shell")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


class TerminalSession:
    """一个工作区拥有的终端：PTY 进程 + 服务端屏幕 + 若干条客户端队列。

    状态字段只由事件循环线程写。``terminate_process`` 是唯一允许跨线程的成员，
    它只碰 ``proc``。
    """

    def __init__(
        self,
        id: str,
        root: str,
        shell: ShellSpec,
        cwd: str,
        proc: Any,
        cols: int,
        rows: int,
        title: str,
    ) -> None:
        self.id = id
        #: 归属工作区。切换工作区不清会话，配额与回收都按它分组。
        self.root = root
        self.shell = shell
        self.cwd = cwd
        self.proc = proc
        self.title = title
        self.screen = TerminalScreen(cols, rows, _settings.scrollback)
        self.closed = False
        self.exit_code: int | None = None
        self.last_output_at = time.monotonic()
        self.finished_at = 0.0
        self._followers: list[_Follower] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 附着
    # ------------------------------------------------------------------

    def attach(self) -> tuple[_Follower, dict[str, Any]]:
        """入队并取首帧。

        两件事必须在同一个同步块里完成：先入队，之后到达的输出才会进这条队列；
        再取屏幕快照，快照才不会与队列内容重复或错位。本方法只在事件循环线程
        调用，与 :meth:`push` / :meth:`settle` 天然串行。

        已退出的会话补一个 ``None`` 哨兵：否则这条队列永远等不到终态，客户端
        会把一个已经死掉的 shell 显示成运行中。

        @returns 附着句柄与客户端必须先收到的 ``snapshot`` 帧。
        """
        follower = _Follower(queue=asyncio.Queue())
        self._followers.append(follower)
        if self.closed:
            follower.queue.put_nowait(None)
        return follower, self.snapshot_frame()

    def detach(self, follower: _Follower) -> None:
        """移除一条附着。幂等。"""
        if follower in self._followers:
            self._followers.remove(follower)

    def snapshot_frame(self) -> dict[str, Any]:
        """一帧完整屏幕，供客户端 ``reset()`` + ``write()`` 后精确重建视口。"""
        return {
            "type": "snapshot",
            "cols": self.screen.cols,
            "rows": self.screen.rows,
            "screen": self.screen.snapshot(),
            "state": "exited" if self.closed else "running",
            "exitCode": self.exit_code,
            "shell": self.shell.id,
            "cwd": self.cwd,
            "title": self.title,
        }

    def info(self) -> dict[str, Any]:
        """注册表视图：不含屏幕内容，够客户端列终端用。"""
        return {
            "id": self.id,
            "root": self.root,
            "shell": self.shell.id,
            "label": self.shell.label,
            "cwd": self.cwd,
            "cols": self.screen.cols,
            "rows": self.screen.rows,
            "state": "exited" if self.closed else "running",
            "exitCode": self.exit_code,
            "title": self.title,
        }

    def attached(self) -> bool:
        return bool(self._followers)

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def push(self, data: bytes) -> None:
        """喂屏幕并扇出给所有客户端。只在事件循环线程调用。

        慢客户端不会被静默丢数据：它的队列超出上限时被标记脱节并收到
        ``RESYNC``，处理方据此补发一帧新快照，客户端重建而不是显示残缺的
        屏幕。脱节期间该附着不再接收输出，避免积压继续增长。
        """
        if self.closed:
            return
        self.screen.feed(data)
        self.last_output_at = time.monotonic()
        limit = _settings.max_queue_bytes
        for follower in tuple(self._followers):
            if follower.desynced:
                continue
            if follower.pending + len(data) > limit:
                follower.desynced = True
                follower.pending = 0
                follower.queue.put_nowait(RESYNC)
                continue
            follower.pending += len(data)
            follower.queue.put_nowait(data)

    def consume(self, follower: _Follower, item: bytes | None | _Resync) -> None:
        """处理方取走一个条目后回扣积压字节数；``RESYNC`` 会解除脱节标记。"""
        if item is RESYNC:
            follower.desynced = False
        elif isinstance(item, bytes):
            follower.pending = max(0, follower.pending - len(item))

    def resynced_frame(self, follower: _Follower) -> dict[str, Any] | None:
        """该附着是否需要一帧新快照；需要则同时解除脱节标记。"""
        if not follower.desynced:
            return None
        follower.desynced = False
        return self.snapshot_frame()

    # ------------------------------------------------------------------
    # 输入与尺寸
    # ------------------------------------------------------------------

    async def write(self, text: str) -> None:
        """把原始输入写进 PTY。

        阻塞写放到工作线程；不改会话状态，因此跨线程安全。已退出的会话直接
        丢弃：PTY 上的写入只会抛异常，而客户端马上就会收到 exit 帧。
        """
        if self.closed or not text:
            return
        await asyncio.to_thread(self._write_now, text)

    def _write_now(self, text: str) -> None:
        try:
            self.proc.write(text)
        except Exception:
            logger.debug("terminal %s: write failed", self.id, exc_info=True)

    async def resize(self, cols: int, rows: int) -> None:
        """先改 PTY 再改屏幕，顺序与输出流一致。

        PTY 重设尺寸可能让 shell 重画，那部分输出会按旧尺寸进来，随后由
        ``TerminalScreen.resize`` 重排。屏幕变更在事件循环线程完成，所以与
        并发到达的 :meth:`push` 不会交错。
        """
        if self.closed:
            return
        await asyncio.to_thread(self._setwinsize_now, rows, cols)
        self.screen.resize(cols, rows)

    def _setwinsize_now(self, rows: int, cols: int) -> None:
        try:
            self.proc.setwinsize(rows, cols)
        except Exception:
            logger.debug("terminal %s: setwinsize failed", self.id, exc_info=True)

    # ------------------------------------------------------------------
    # 终态
    # ------------------------------------------------------------------

    def settle(self, code: int | None) -> None:
        """标记终态并通知所有客户端。只在事件循环线程调用。"""
        if self.closed:
            return
        self.closed = True
        self.exit_code = 0 if code is None else int(code)
        self.finished_at = time.monotonic()
        for follower in tuple(self._followers):
            follower.queue.put_nowait(None)

    def terminate_process(self) -> int | None:
        """终止进程树并有界地取退出码。

        可以在读线程或工作线程调用：只碰 ``proc``，不改任何会话状态。阻塞等待
        有上界，孙进程持有管道时也不会把调用方拖住。
        """
        with self._lock:
            if self.proc is None:
                return None
            try:
                self.proc.terminate(force=True)
            except Exception:
                logger.debug("terminal %s: terminate failed", self.id, exc_info=True)
            code: int | None = None
            try:
                if not self.proc.isalive():
                    code = self.proc.exitstatus
            except Exception:
                code = None
            return code

    def dispose(self) -> None:
        """终止进程并从注册表摘除。只在事件循环线程调用。"""
        self.terminate_process()
        self.settle(None)
        self._sessions_drop()

    def _sessions_drop(self) -> None:
        if _sessions.get(self.id) is self:
            del _sessions[self.id]


# ----------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------


def live_count(root: str) -> int:
    """该工作区仍然存活的终端数。已退出的不占配额。"""
    return sum(1 for s in _sessions.values() if s.root == root and not s.closed)


def list_for_root(root: str) -> list[TerminalSession]:
    """按创建顺序返回该工作区的全部终端，含已退出的。"""
    return [s for s in _sessions.values() if s.root == root]


def reap(now: float | None = None) -> list[str]:
    """回收无主的终端，返回被摘除的 id 供调用方记日志。

    两种回收：已退出且过了退出宽限期——宽限期让客户端切回工作区时还能看到
    退出码；无客户端且长时间无输出——``idle_ttl_s`` 为 0 时不按空闲回收。
    有客户端附着的会话永不回收。
    """
    moment = now if now is not None else time.monotonic()
    config = _settings
    removed: list[str] = []
    for session in list(_sessions.values()):
        if session.attached():
            continue
        if session.closed:
            if config.exit_ttl_s and moment - session.finished_at >= config.exit_ttl_s:
                removed.append(session.id)
        elif config.idle_ttl_s and moment - session.last_output_at >= config.idle_ttl_s:
            removed.append(session.id)
    for session_id in removed:
        session = _sessions.get(session_id)
        if session is not None:
            session.dispose()
    if removed:
        logger.info("reaped %d unattended terminal(s): %s", len(removed), ", ".join(removed))
    return removed


def shutdown_terminals() -> None:
    """终止本进程内的全部终端。可从工作线程调用（lifespan 用 to_thread 调）。"""
    for session in list(_sessions.values()):
        try:
            session.terminate_process()
        except Exception:
            logger.debug("terminal %s: shutdown terminate failed", session.id, exc_info=True)
    _sessions.clear()


# ----------------------------------------------------------------------
# 进程启动
# ----------------------------------------------------------------------


def _meipass() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    bundle = getattr(sys, "_MEIPASS", None)
    return str(bundle) if bundle else None


def _child_env() -> dict[str, str] | None:
    """去掉传给 shell 的打包目录和 _PYI_*，避免 Git Bash 加载到包里的 DLL。"""
    bundle = _meipass()
    if bundle is None:
        return None
    env = dict(os.environ)
    norm = os.path.normcase(os.path.abspath(bundle))
    kept: list[str] = []
    for item in env.get("PATH", "").split(os.pathsep):
        if not item:
            continue
        try:
            if os.path.normcase(os.path.abspath(item)) == norm:
                continue
        except OSError:
            continue
        kept.append(item)
    env["PATH"] = os.pathsep.join(kept)
    for key in list(env):
        if key.startswith("_PYI") or key in {"_MEIPASS", "_MEIPASS2"}:
            del env[key]
    return env


@contextmanager
def _bundle_dll_search_suspended() -> Iterator[None]:
    """PyInstaller 用 SetDllDirectoryW 把 DLL 搜索指到 _MEIPASS，子进程会继承。"""
    bundle = _meipass()
    if os.name != "nt" or bundle is None:
        yield
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel32.SetDllDirectoryW.restype = ctypes.c_int
    kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        kernel32.SetDllDirectoryW(bundle)


def _require_conpty_host() -> None:
    if _meipass() is None:
        return
    import winpty

    directory = Path(winpty.__file__).resolve().parent
    host = directory / "OpenConsole.exe"
    dll = directory / "conpty.dll"
    if host.is_file() and dll.is_file():
        return
    raise OSError(f"缺少 ConPTY 宿主，需要 {dll.name} 与 {host.name} 在同一目录: {directory}")


def _conpty_init_panic(exc: BaseException) -> bool:
    """winpty-rs 把 ShowWindow 返回 0 当成失败。窗口本来就隐藏时，错误码是 S_OK。"""
    return type(exc).__name__ == "PanicException" and "HRESULT(0x00000000)" in str(exc)


def _ensure_hidden_console() -> None:
    """没有控制台时先分配一个并藏起来，避免 pywinpty 第一次初始化 panic。

    windowed sidecar 由 Electron 以隐藏方式拉起。pywinpty 会 AllocConsole，
    再 ``ShowWindow(SW_HIDE).unwrap()``。这个控制台生下来就是隐藏的，
    ShowWindow 返回 0 且 GetLastError 为 0，unwrap 抛出 PanicException。
    控制台已经存在时，pywinpty 不会再走这段，所以第二次创建终端是好的。
    """
    if os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    kernel32.AllocConsole.restype = ctypes.c_int
    if kernel32.GetConsoleWindow():
        return
    if not kernel32.AllocConsole():
        return
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_int
    user32.ShowWindow(hwnd, _SW_HIDE)


def _spawn_pty(argv: list[str], kwargs: dict[str, Any]) -> Any:
    from winpty import PtyProcess

    try:
        return PtyProcess.spawn(argv, **kwargs)
    except BaseException as exc:
        if not _conpty_init_panic(exc):
            raise
        logger.warning("ConPTY 首次初始化失败，重试一次: %s", exc)
        return PtyProcess.spawn(argv, **kwargs)


def _spawn(spec: ShellSpec, cwd: str, cols: int, rows: int) -> Any:
    if os.name == "nt":
        kwargs: dict[str, Any] = {"cwd": cwd, "dimensions": (rows, cols)}
        env = _child_env()
        if env is not None:
            kwargs["env"] = env
        with _spawn_lock, _bundle_dll_search_suspended():
            _require_conpty_host()
            _ensure_hidden_console()
            return _WinPtyProcess(_spawn_pty(spec.argv, kwargs))
    import pty
    import subprocess

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            spec.argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            start_new_session=True,
        )
    except Exception:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    return _PosixPty(master, proc)


class _WinPtyProcess:
    """把 pywinpty 的 ``str`` 接口归一成字节。

    winpty 按控制台代码页把 PTY 字节解成 ``str``，而 POSIX 侧拿到的是原始字节。
    两种 PTY 若不统一，喂给模拟器的数据类型就会随平台变化。这里把 winpty 的
    字符串编回 UTF-8：shell 启动参数里的 ``chcp 65001`` 保证往返无损，于是
    "``proc.read()`` 一定返回字节"成为 PTY 层的统一契约，模拟器、队列和 socket
    全程不再做编解码往返。
    """

    __slots__ = ("_proc",)

    def __init__(self, proc: Any) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return self._proc.pid

    def read(self, size: int) -> bytes:
        data = self._proc.read(size)
        return data if isinstance(data, bytes) else data.encode("utf-8", "replace")

    def write(self, text: str) -> Any:
        return self._proc.write(text)

    def setwinsize(self, rows: int, cols: int) -> Any:
        return self._proc.setwinsize(rows, cols)

    def isalive(self) -> bool:
        return self._proc.isalive()

    @property
    def exitstatus(self) -> int | None:
        return self._proc.exitstatus

    def terminate(self, force: bool = False) -> Any:
        return self._proc.terminate(force=force)


class _PosixPty:
    """POSIX 上的最小 PTY 适配：只有终端功能真正用到的那几个调用。"""

    def __init__(self, master: int, proc: Any) -> None:
        self._master = master
        self._proc = proc

    def read(self, size: int) -> bytes:
        data = os.read(self._master, size)
        if not data:
            raise EOFError
        return data

    def write(self, text: str) -> int:
        return os.write(self._master, text.encode("utf-8"))

    def setwinsize(self, rows: int, cols: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def isalive(self) -> bool:
        return self._proc.poll() is None

    @property
    def exitstatus(self) -> int | None:
        return self._proc.poll()

    def terminate(self, force: bool = False) -> None:
        """杀掉进程组。

        不在这里 ``close(master)``：读线程可能正阻塞在这个 fd 的 ``os.read`` 上，
        Linux 上关闭被阻塞读的 fd 不会唤醒读者，线程会挂死，且被回收的 fd 号
        可能被下一个会话复用后偷走它的输出。读线程会因 EOF 自行退出，进程组的
        文件描述符由内核在最后一个引用消失时回收。
        """
        if self._proc.poll() is None:
            import signal

            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGHUP)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                self._proc.kill() if force else self._proc.terminate()
            except OSError:
                pass


def _read_loop(session: TerminalSession, loop: asyncio.AbstractEventLoop) -> None:
    """把 PTY 原始字节搬到事件循环。

    循环关闭后立刻退出而不是继续 ``call_soon_threadsafe``：那是跨线程调用已停
    止的 loop，会在 daemon 线程里抛 ``RuntimeError`` 并丢掉剩下的输出。
    """
    while True:
        if loop.is_closed():
            return
        try:
            chunk = session.proc.read(4096)
        except EOFError:
            break
        except Exception:
            logger.debug("terminal %s: read failed", session.id, exc_info=True)
            break
        if not chunk:
            break
        try:
            loop.call_soon_threadsafe(session.push, chunk)
        except RuntimeError:
            return
    code: int | None = None
    try:
        if not session.proc.isalive():
            code = session.proc.exitstatus
    except Exception:
        code = None
    try:
        if not loop.is_closed():
            loop.call_soon_threadsafe(session.settle, code)
    except RuntimeError:
        return


def start_reader(session: TerminalSession, loop: asyncio.AbstractEventLoop) -> None:
    """为会话启动读线程。"""
    threading.Thread(target=_read_loop, args=(session, loop), daemon=True).start()


def _cwd(root_raw: str, cwd_raw: str | None) -> tuple[Path, Path]:
    root = _workspace_root(root_raw)
    if not cwd_raw or not cwd_raw.strip():
        return root, root
    try:
        target = Path(cwd_raw).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="路径无效") from exc
    if not _within_root(root, target) or not target.is_dir():
        raise HTTPException(status_code=403, detail="终端目录必须在工作区里")
    return root, target
