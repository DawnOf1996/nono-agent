"""shell 方言解析与 argv 构造的测试。

平台分支全部通过 ``platform=`` 显式注入，测试在任何机器上结果一致
（平台差异收敛到单点函数是仓库惯例，见 utils/process.py）。
"""

from __future__ import annotations

import sys

import pytest

from nono_agent.shell.dialect import (
    DIALECT_NAMES,
    build_argv,
    env_overrides,
    resolve_dialect,
)
from nono_agent.shell.preamble import PS_EXIT_WRAP, PS_PREAMBLE


class TestResolveDialect:
    def test_auto_picks_sh_on_posix(self):
        assert resolve_dialect("auto", platform="linux").name == "sh"
        assert resolve_dialect("auto", platform="darwin").name == "sh"

    def test_auto_on_windows_prefers_pwsh(self, monkeypatch):
        monkeypatch.setattr(
            "nono_agent.shell.dialect.shutil.which", lambda exe: f"C:\\{exe}"
        )
        assert resolve_dialect("auto", platform="win32").name == "pwsh"

    def test_auto_on_windows_falls_back_to_powershell(self, monkeypatch):
        # 目标机实测：未安装 pwsh 7，只有 Windows PowerShell 5.1
        monkeypatch.setattr(
            "nono_agent.shell.dialect.shutil.which",
            lambda exe: None if exe == "pwsh.exe" else f"C:\\{exe}",
        )
        assert resolve_dialect("auto", platform="win32").name == "powershell"

    def test_auto_on_windows_without_any_shell(self, monkeypatch):
        monkeypatch.setattr("nono_agent.shell.dialect.shutil.which", lambda exe: None)
        assert resolve_dialect("auto", platform="win32").name == "powershell"

    @pytest.mark.parametrize("name", ["powershell", "pwsh", "cmd", "bash", "sh"])
    def test_explicit_name_wins_over_auto(self, name):
        assert resolve_dialect(name, platform="win32").name == name
        assert resolve_dialect(name, platform="linux").name == name

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="unknown shell dialect"):
            resolve_dialect("nope")

    def test_default_is_auto(self):
        assert resolve_dialect().name == resolve_dialect("auto").name

    def test_dialect_names_lists_all(self):
        assert set(DIALECT_NAMES) == {"auto", "bash", "cmd", "powershell", "pwsh", "sh"}


class TestBuildArgv:
    def test_powershell_argv_shape(self):
        dialect = resolve_dialect("pwsh", platform="win32")
        argv = build_argv(dialect, "Get-Process")
        assert tuple(argv[:5]) == (
            "pwsh.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
        )
        assert argv[-1] == f"{PS_PREAMBLE}Get-Process; {PS_EXIT_WRAP}"
        assert len(argv) == 6  # 前缀 5 + 命令 1，命令整体是一个 argv 元素

    def test_command_is_single_argv_element(self):
        # 引号转义层的消解证据：带空格/引号/管道/重定向的命令不会被拆词。
        dialect = resolve_dialect("sh", platform="linux")
        command = 'python -c "print(1)" | tee out.txt > log 2>&1 && echo "a b"'
        argv = build_argv(dialect, command)
        assert len(argv) == 3
        assert command in argv[-1]

    def test_no_newline_injected(self):
        # 换行会破坏 PowerShell 的错误行号（声明与命令必须留在同一行）
        for name in ("powershell", "pwsh", "cmd", "bash", "sh"):
            argv = build_argv(resolve_dialect(name), "echo hi")
            assert "\n" not in argv[-1]

    def test_cmd_dialect_has_no_powershell_suffix(self):
        # $? 守卫是 PowerShell 语法，套到 cmd 上会直接语法错误
        argv = build_argv(resolve_dialect("cmd"), "dir")
        assert not argv[-1].endswith(PS_EXIT_WRAP)
        assert argv[-1].startswith("chcp 65001>nul & ")

    def test_posix_dialect_has_no_powershell_suffix(self):
        for name in ("sh", "bash"):
            argv = build_argv(resolve_dialect(name), "ls")
            assert not argv[-1].endswith(PS_EXIT_WRAP)
            assert argv[-1].startswith("export PYTHONIOENCODING=utf-8; ")

    def test_command_containing_semicolon_is_not_split(self):
        argv = build_argv(resolve_dialect("sh"), "a; b; c")
        assert argv[-1] == "export PYTHONIOENCODING=utf-8; a; b; c"


class TestEnvOverrides:
    def test_disables_colors_and_pagers(self):
        env = env_overrides()
        assert env["NO_COLOR"] == "1"
        assert env["PAGER"] == "cat"
        assert env["GIT_PAGER"] == "cat"

    def test_extra_overrides_base(self):
        env = env_overrides({"PYTHONWARNINGS": "ignore", "NO_COLOR": "0"})
        assert env["PYTHONWARNINGS"] == "ignore"
        assert env["NO_COLOR"] == "0"  # 显式覆盖优先
        assert env["PAGER"] == "cat"  # 其余保留

    def test_returns_a_copy(self):
        first = env_overrides()
        first["PAGER"] = "mutated"
        assert env_overrides()["PAGER"] == "cat"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell 方言是 Windows-only")
class TestRealPowerShell:
    """用真实 powershell 验证 argv 确实可执行 —— mock 掩盖不了引号与语法问题。"""

    async def _run(self, argv):
        import asyncio
        import os

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, **env_overrides()},
        )
        out, err = await proc.communicate()
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def test_chinese_output_is_not_mojibake(self):
        # 编码前置的核心价值：PowerShell 自身输出与错误信息不再乱码
        argv = build_argv(resolve_dialect("auto", platform="win32"), 'Write-Output "中文"')
        _, out, _ = await self._run(argv)
        assert out.strip() == "中文"

    async def test_error_message_is_readable(self):
        argv = build_argv(
            resolve_dialect("auto", platform="win32"), "Get-Item 'C:\\nope\\missing.txt'"
        )
        code, _, err = await self._run(argv)
        assert code == 1
        assert "missing.txt" in err
        # 编码前置的核心断言：**不含替换字符**。
        # PowerShell 的消息语言随 $PSUICulture 变化（中文系统上是「找不到路径」，
        # 英文系统上是 "Cannot find path"），所以不能断言具体语言 ——
        # 不前置编码声明时非 ASCII 消息按控制台代码页写出，按 UTF-8 解码必然出现
        # U+FFFD。这里断言的是「能正确解码」这个性质本身。
        assert "�" not in err

    async def test_native_exit_code_is_preserved(self):
        # 裸 -Command 会把 python 的 exit 3 压成 1；$? 守卫包装后必须是 3
        argv = build_argv(
            resolve_dialect("auto", platform="win32"),
            f'& "{sys.executable}" -c "import sys; sys.exit(3)"',
        )
        code, _, _ = await self._run(argv)
        assert code == 3

    async def test_no_false_failure_after_recovery(self):
        # 失败原生命令后跟成功 cmdlet 必须报 0（简单 $LASTEXITCODE 包装会误报 3）
        argv = build_argv(
            resolve_dialect("auto", platform="win32"),
            f'& "{sys.executable}" -c "import sys; sys.exit(3)"; Get-Date',
        )
        code, _, _ = await self._run(argv)
        assert code == 0

    async def test_pipeline_still_works(self):
        # 去掉中间 shell 的代价检查：PowerShell 自带管道
        argv = build_argv(
            resolve_dialect("auto", platform="win32"), "Write-Output 'a' | Measure-Object | "
            "ForEach-Object { $_.Count }"
        )
        code, out, _ = await self._run(argv)
        assert code == 0
        assert out.strip() == "1"
