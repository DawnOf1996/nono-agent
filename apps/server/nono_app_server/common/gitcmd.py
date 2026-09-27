"""调用系统 git：每次请求起一个进程，输出用 -z / 分隔符格式，解析不依赖语言与颜色配置。"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from nono_agent.utils.process import hidden_kwargs

GIT_TIMEOUT_S = 30.0
GIT_OUTPUT_MAX_BYTES = 32 * 1024 * 1024


class GitError(Exception):
    """git 不可用、超时或以非零状态退出。"""

    def __init__(self, message: str, *, code: int = -1) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class GitResult:
    code: int
    stdout: bytes
    stderr: str

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


def git_available() -> bool:
    return shutil.which("git") is not None


def _env(extra: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "LC_ALL": "C",
        }
    )
    if extra:
        env.update(extra)
    return env


async def run_git(
    cwd: str | Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = GIT_TIMEOUT_S,
    ok_codes: tuple[int, ...] = (0,),
    input: bytes | None = None,
) -> GitResult:
    """运行一条 git 命令；退出码不在 ok_codes 里时抛 GitError（stderr 作消息）。"""
    full = ["git", "-c", "core.quotepath=false", "-c", "color.ui=false", "--no-pager", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *full,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_env(env),
            **hidden_kwargs(),
        )
    except (OSError, ValueError) as exc:
        raise GitError(f"无法运行 git：{exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(input), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise GitError(f"git {args[0] if args else ''} 超时") from exc
    code = proc.returncode if proc.returncode is not None else -1
    if len(stdout) > GIT_OUTPUT_MAX_BYTES:
        raise GitError("git 输出过大")
    message = (stderr or b"").decode("utf-8", errors="replace").strip()
    if code not in ok_codes:
        detail = message or stdout.decode("utf-8", errors="replace").strip()
        raise GitError(detail or f"git 以状态 {code} 退出", code=code)
    return GitResult(code=code, stdout=stdout, stderr=message)


def path_identity(path: str | Path) -> str:
    """只用于比较绝对路径是否相同（Windows 不分大小写、分隔符统一）。"""
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = str(path)
    resolved = resolved.rstrip("\\/") or resolved
    return os.path.normcase(resolved)


async def repo_toplevel(cwd: str | Path) -> Path | None:
    """cwd 所在仓库的工作树根目录；不在仓库里返回 None。"""
    try:
        result = await run_git(cwd, "rev-parse", "--show-toplevel", timeout=10)
    except GitError:
        return None
    top = result.text.strip()
    return Path(top) if top else None


async def git_path(cwd: str | Path, name: str) -> Path:
    """`git rev-parse --git-path <name>` 的绝对路径（兼容会输出相对路径的旧版 git）。"""
    result = await run_git(cwd, "rev-parse", "--git-path", name, timeout=10)
    raw = Path(result.text.strip())
    return raw if raw.is_absolute() else (Path(cwd) / raw).resolve()
