"""Spike：验证 PowerShell 方言的退出码语义与编码前置效果。

支撑 `docs-site/docs/architecture/shell-tool.md` §D2 的结论。只读观测，不改任何仓库代码。

为什么需要它：`powershell -Command "<cmd>"` 不传播原生命令的 $LASTEXITCODE，
而 bash 工具的输出格式（`[exit code: N]`）直接依赖退出码正确。DSH 也没解决
（`dsh-pwsh-local/lib/index.js:197-206` 同样是裸 -Command），所以这是 PowerShell
本身的语义，必须实测而不是推断。

用法（需 Windows）::

    python scripts/spike_pwsh_dialect.py

新增用例时请保持「裸执行」与「加包装」两列同时出现 —— 差值才是决策依据。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

PS_ENCODING = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)
PS_ARGS = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")

# 两个候选包装。差异见下表：WRAP 在「失败原生命令后跟成功 cmdlet」时误报。
WRAP = "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }"
WRAP2 = (
    "if (-not $?) { if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }"
)


def hidden_kwargs() -> dict[str, int]:
    """与 nono_agent/utils/process.py:15 保持一致。"""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


async def run_ps(
    command: str,
    *,
    preamble: str = PS_ENCODING,
    suffix: str = "",
    env_extra: dict[str, str] | None = None,
    strip_env: str | None = None,
) -> tuple[int, str, str]:
    env = dict(os.environ)
    if strip_env:
        env.pop(strip_env, None)
    if env_extra:
        env.update(env_extra)
    tail = f"; {suffix}" if suffix else ""
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe", *PS_ARGS, f"{preamble}{command}{tail}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=env,
        **hidden_kwargs(),
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def show(value: str, limit: int = 46) -> str:
    return value.strip().replace("\r\n", " | ")[:limit]


async def main() -> None:
    if sys.platform != "win32":
        print("此 spike 只在 Windows 上有意义。")
        return

    py = sys.executable
    py_exit3 = f'& "{py}" -c "import sys; sys.exit(3)"'
    py_exit0 = f'& "{py}" -c "import sys; sys.exit(0)"'
    py_cn = f'& "{py}" -c "print(chr(20013)+chr(25991))"'

    print(f"python = {py}\n")

    print("=== 1. 退出码：裸执行 vs 两种归一化包装 ===")
    print(f"{'场景':<34} {'裸':>5} {'WRAP':>6} {'WRAP2':>7}")
    cases: list[tuple[str, str]] = [
        ("原生命令 exit(3)", py_exit3),
        ("原生命令 exit(0)", py_exit0),
        ("失败 cmdlet", "Get-Item 'C:\\nope\\x.txt'"),
        ("成功 cmdlet", "Get-Date"),
        ("exit 3", "exit 3"),
        ("失败后跟成功 cmdlet（误报边界）", f"{py_exit3}; Get-Date"),
    ]
    for label, command in cases:
        bare, *_ = await run_ps(command)
        w1, *_ = await run_ps(command, suffix=WRAP)
        w2, *_ = await run_ps(command, suffix=WRAP2)
        print(f"{label:<34} {bare:>5} {w1:>6} {w2:>7}")

    print("\n=== 2. 编码前置（不设 PYTHONIOENCODING）===")
    # 注意：PowerShell 的错误信息走 stderr，Write-Output 走 stdout，两列都要看。
    for label, command, channel in [
        ("PowerShell Write-Output 中文", 'Write-Output "中文测试"', 0),
        ("PowerShell 错误消息", "Get-Item 'C:\\nope\\y.txt'", 1),
        ("子进程 python 中文", py_cn, 0),
    ]:
        _, out_w, err_w = await run_ps(command, strip_env="PYTHONIOENCODING")
        _, out_wo, err_wo = await run_ps(
            command, preamble="", strip_env="PYTHONIOENCODING"
        )
        pick = lambda o, e: e if channel == 1 else o  # noqa: E731
        mark = "stderr" if channel == 1 else "stdout"
        print(
            f"{label:<26} {mark} 前置={show(pick(out_w, err_w), 20)!r:<24}"
            f" 不前置={show(pick(out_wo, err_wo), 20)!r}"
        )

    print("\n=== 3. 显式 argv 下的管道 / 重定向（去掉中间 shell 的代价检查）===")
    for label, command in [
        ("管道", "Get-Process | Select-Object -First 2 Id"),
        ("重定向", 'Set-Content -Path "$env:TEMP/_spike.txt" -Value "x"; '
                   '(Get-Item "$env:TEMP/_spike.txt").Length'),
    ]:
        code, out, _ = await run_ps(command, suffix=WRAP2)
        print(f"{label:<28} exit={code:<4} out={show(out)!r}")

    print("\n期望：§D2 表格中 WRAP2 全部正确，编码前置只对 PowerShell 自身输出生效。")


if __name__ == "__main__":
    asyncio.run(main())
