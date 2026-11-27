"""Shell 方言解析与 argv 构造。

工具不再走 ``create_subprocess_shell(shell=True)``，而是显式构造 argv。
命令作为**一个 argv 元素**整体传给 shell 解析，因此不存在中间 shell 的
引号转义层 —— PowerShell 与 sh 各自按自己的语法解析整段文本。

方言（preamble + 后缀）见 :mod:`nono_agent.shell.preamble`；选哪个方言
（preamble/suffix/exe）由 :class:`ShellDialect` 描述。
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Final

from nono_agent.shell.preamble import (
    CMD_PREAMBLE,
    POSIX_PREAMBLE,
    PS_EXIT_WRAP,
    PS_PREAMBLE,
)

_PS_ARGS: Final = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")

# 模型友好的环境覆盖：禁颜色、禁分页器，否则工具输出被 ANSI 控制码污染。
# ``TERM=dumb`` 是 POSIX 概念，不设（现代 pwsh 认 NO_COLOR）。
_BASE_ENV_OVERRIDES: Final[dict[str, str]] = {
    "NO_COLOR": "1",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
}


@dataclass(frozen=True)
class ShellDialect:
    """一个 shell 方言：可执行文件、argv 前缀、编码声明、退出码归一化后缀。"""

    name: str
    exe: str
    argv_prefix: tuple[str, ...]
    preamble: str
    suffix: str


_DIALECTS: Final[dict[str, ShellDialect]] = {
    "powershell": ShellDialect(
        "powershell", "powershell.exe", ("powershell.exe", *_PS_ARGS), PS_PREAMBLE, PS_EXIT_WRAP
    ),
    "pwsh": ShellDialect(
        "pwsh", "pwsh.exe", ("pwsh.exe", *_PS_ARGS), PS_PREAMBLE, PS_EXIT_WRAP
    ),
    # cmd 的退出码由 %ERRORLEVEL% 决定，不套用 PowerShell 的 $? 守卫（语法不通）。
    "cmd": ShellDialect("cmd", "cmd.exe", ("cmd.exe", "/D", "/S", "/C"), CMD_PREAMBLE, ""),
    "bash": ShellDialect("bash", "bash", ("bash", "-c"), POSIX_PREAMBLE, ""),
    "sh": ShellDialect("sh", "sh", ("sh", "-c"), POSIX_PREAMBLE, ""),
}

DIALECT_NAMES: Final[tuple[str, ...]] = (*sorted(_DIALECTS), "auto")


def resolve_dialect(name: str = "auto", *, platform: str | None = None) -> ShellDialect:
    """解析方言。

    ``auto``：Windows 优先 pwsh 7（退出码与流行为更规整），退回 Windows
    PowerShell 5.1；POSIX 用 sh。

    ``platform`` 显式注入而非直读 ``sys.platform``，让平台分支可测 —— 仓库惯例
    是把平台差异收敛到单点函数（``utils/process.py``），不在调用点散落分支。
    """
    if name != "auto":
        try:
            return _DIALECTS[name]
        except KeyError:
            raise ValueError(
                f"unknown shell dialect {name!r}; expected one of {list(DIALECT_NAMES)}"
            ) from None

    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        for candidate in ("pwsh", "powershell"):
            dialect = _DIALECTS[candidate]
            if shutil.which(dialect.exe):
                return dialect
        return _DIALECTS["powershell"]  # 都探测不到时的最后兜底
    return _DIALECTS["sh"]


def build_argv(dialect: ShellDialect, command: str) -> list[str]:
    """构造完整 argv。

    形态 ``[*prefix, preamble + command + suffix]``。前缀、声明、后缀全用分号
    衔接且不引入换行 —— 命令始终在第 1 行，PowerShell 的错误行号因此保持准确。
    """
    tail = f"; {dialect.suffix}" if dialect.suffix else ""
    return [*dialect.argv_prefix, f"{dialect.preamble}{command}{tail}"]


def env_overrides(extra: dict[str, str] | None = None) -> dict[str, str]:
    """本次执行叠加到 ``os.environ`` 上的环境覆盖。"""
    if not extra:
        return dict(_BASE_ENV_OVERRIDES)
    return {**_BASE_ENV_OVERRIDES, **extra}


__all__ = [
    "DIALECT_NAMES",
    "ShellDialect",
    "build_argv",
    "env_overrides",
    "resolve_dialect",
]
