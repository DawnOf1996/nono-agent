"""编码声明与退出码归一化 —— 在命令执行前钉死两件 PowerShell 会弄错的事。

命令文本的编码在消费端猜是消费端的事（``tools/builtin/_common.py`` 的
utf-8 → locale → gb18030 回退链）；但猜错的代价是整段输出乱码。把声明前置到
生产端，这条回退链就退化成兜底而非常规路径。

⚠️ 声明的作用范围仅限 **PowerShell 自身产生的输出**（Write-Output、错误消息、
格式化表格）。它 **不改变子进程的 stdout 编码** —— 子进程自行决定。
实测见 ``scripts/spike_pwsh_dialect.py``。
"""

from __future__ import annotations

# PowerShell：分号分隔而非换行，保证错误行号不错位。
PS_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)

# 退出码归一化，追加在命令**之后**。
#
# 裸 `powershell -Command "<cmd>"` 不传播原生命令的 $LASTEXITCODE —— 实测
# `python -c "sys.exit(3)"` 会被压成 1，模型无法区分「测试失败」和「命令跑错」。
# 简单包装 `if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }` 又会在
# 「失败原生命令后跟成功 cmdlet」时把陈旧值误当终态。这里用 $? 守卫消除误报。
#
# 完整实测表见 docs/Shell-Tool-Design.md §D2。
PS_EXIT_WRAP = (
    "if (-not $?) { if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }"
)

# cmd：`chcp 65001` 切代码页。用 & 而非 ; 分隔（cmd 不认 ;）。
CMD_PREAMBLE = "chcp 65001>nul & "

# POSIX：只设 PYTHONIOENCODING。不设 LC_ALL=C.UTF-8 —— macOS 没有该 locale，
# 设了会得到 "cannot change locale" 警告，反而污染输出。
POSIX_PREAMBLE = "export PYTHONIOENCODING=utf-8; "

__all__ = [
    "CMD_PREAMBLE",
    "POSIX_PREAMBLE",
    "PS_EXIT_WRAP",
    "PS_PREAMBLE",
]
