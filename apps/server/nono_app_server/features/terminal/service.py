"""持久终端会话：选择 shell、拉起进程、保留断线后的输出。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from nono_app_server.common.paths import (
    within_root as _within_root,
)
from nono_app_server.common.paths import (
    workspace_root as _workspace_root,
)

_MAX_SESSIONS = 8

_BACKLOG_CHARS = 200_000

_sessions: dict[str, TerminalSession] = {}


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


@dataclass
class TerminalSession:
    id: str
    shell: ShellSpec
    cwd: str
    proc: Any
    backlog: str = ""
    exit_code: int | None = None
    closed: bool = False
    _queues: list[asyncio.Queue[str | None]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def attach(self) -> asyncio.Queue[str | None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._queues.append(queue)
        return queue

    def detach(self, queue: asyncio.Queue[str | None]) -> None:
        if queue in self._queues:
            self._queues.remove(queue)

    def push(self, text: str) -> None:
        if not text or self.closed:
            return
        self.backlog = (self.backlog + text)[-_BACKLOG_CHARS:]
        for queue in self._queues:
            queue.put_nowait(text)

    def finish(self, code: int | None) -> None:
        if self.closed:
            return
        self.closed = True
        self.exit_code = 0 if code is None else int(code)
        for queue in self._queues:
            queue.put_nowait(None)

    async def write(self, text: str) -> None:
        if self.closed or not text:
            return
        await asyncio.to_thread(self.proc.write, text)

    async def resize(self, rows: int, cols: int) -> None:
        if self.closed:
            return
        await asyncio.to_thread(self.proc.setwinsize, rows, cols)

    def kill(self) -> None:
        if self.closed:
            self._sessions_drop()
            return
        try:
            self.proc.terminate(force=True)
        except Exception:
            pass
        code = None
        try:
            if not self.proc.isalive():
                code = self.proc.exitstatus
        except Exception:
            code = None
        self.finish(code)
        self._sessions_drop()

    def _sessions_drop(self) -> None:
        _sessions.pop(self.id, None)


def _read_loop(session: TerminalSession, loop: asyncio.AbstractEventLoop) -> None:
    while True:
        try:
            chunk = session.proc.read(4096)
        except EOFError:
            break
        except Exception:
            break
        if chunk:
            loop.call_soon_threadsafe(session.push, chunk)
    code = None
    try:
        if not session.proc.isalive():
            code = session.proc.exitstatus
    except Exception:
        code = None
    loop.call_soon_threadsafe(session.finish, code)


_spawn_lock = threading.Lock()


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


def _spawn(spec: ShellSpec, cwd: str, cols: int, rows: int) -> Any:
    if os.name == "nt":
        from winpty import PtyProcess

        kwargs: dict[str, Any] = {"cwd": cwd, "dimensions": (rows, cols)}
        env = _child_env()
        if env is not None:
            kwargs["env"] = env
        with _spawn_lock, _bundle_dll_search_suspended():
            _require_conpty_host()
            return PtyProcess.spawn(spec.argv, **kwargs)
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

    class _PosixPty:
        def __init__(self) -> None:
            self._master = master
            self._proc = proc

        def read(self, size: int) -> str:
            data = os.read(self._master, size)
            if not data:
                raise EOFError
            return data.decode("utf-8", "replace")

        def write(self, text: str) -> int:
            return os.write(self._master, text.encode("utf-8"))

        def setwinsize(self, rows: int, cols: int) -> None:
            import fcntl
            import struct
            import termios

            buf = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._master, termios.TIOCSWINSZ, buf)

        def isalive(self) -> bool:
            return self._proc.poll() is None

        @property
        def exitstatus(self) -> int | None:
            return self._proc.poll()

        def terminate(self, force: bool = False) -> None:
            if self._proc.poll() is None:
                self._proc.kill() if force else self._proc.terminate()
            os.close(self._master)

    return _PosixPty()


def shutdown_terminals() -> None:
    for session in list(_sessions.values()):
        session.kill()
    _sessions.clear()


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
