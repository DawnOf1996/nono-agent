"""bash 工具的行为测试（对应 docs-site/docs/architecture/shell-tool.md §6.3）。

覆盖重构引入的语义变化：显式 argv、stdout/stderr 分离、超限落盘、
非零退出不再算错误、退出码归一化。

用真实子进程而非 mock —— 引号、编码、进程树这些正是 mock 掩盖不了的部分。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nono_agent.core.config import ShellConfig
from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
from nono_agent.tools.builtin import BashTool
from nono_agent.tools.context import ToolUseContext

IS_WINDOWS = sys.platform == "win32"
# Windows 上默认方言是 PowerShell，调用带引号的可执行文件必须用调用运算符。
CALL = "&" if IS_WINDOWS else ""


def _py(snippet: str) -> str:
    return f'{CALL} "{sys.executable}" -c "{snippet}"'


def _stdout(text: str) -> str:
    return _py(f'import sys; sys.stdout.write({text!r})')


def _stderr(text: str) -> str:
    return _py(f'import sys; sys.stderr.write({text!r})')


def _spill_path_from(content: str) -> str:
    """从渲染结果里取出落盘路径。"""
    line = next(row for row in content.splitlines() if "full output:" in row)
    return line.split("full output:")[1].rstrip("]").strip()


@pytest.fixture
def ctx(tmp_path):
    return ToolUseContext(
        cwd=str(tmp_path),
        sandbox=PathSandbox(tmp_path),
        ignore=IgnoreMatcher.from_workspace(tmp_path),
    )


class TestExitCode:
    async def test_nonzero_exit_is_not_an_error(self, ctx):
        """非零退出以标记上报，由模型判断 —— 不是工具错误。"""
        result = await BashTool().execute({"command": "exit 3"}, ctx)
        assert result.is_error is False
        assert "[exit code: 3]" in result.content

    async def test_clean_exit_has_no_marker(self, ctx):
        result = await BashTool().execute({"command": _stdout("ok")}, ctx)
        assert "exit code" not in result.content

    async def test_native_exit_code_is_preserved(self, ctx):
        """裸 -Command 会把原生失败压成 1；$? 守卫包装后必须还原真实退出码。"""
        result = await BashTool().execute({"command": _py("import sys; sys.exit(3)")}, ctx)
        assert "[exit code: 3]" in result.content

    async def test_no_false_failure_after_recovery(self, ctx):
        """失败原生命令后跟成功 cmdlet 必须报成功。

        简单 $LASTEXITCODE 包装在这里会误报 3（见 §D2 实测表）。
        """
        result = await BashTool().execute(
            {"command": f'{_py("import sys; sys.exit(3)")}; echo recovered'}, ctx
        )
        assert "exit code" not in result.content
        assert "recovered" in result.content

    async def test_exit_zero_from_native_command(self, ctx):
        result = await BashTool().execute({"command": _py("import sys; sys.exit(0)")}, ctx)
        assert "exit code" not in result.content


class TestStreamSeparation:
    async def test_stderr_gets_its_own_section(self, ctx):
        result = await BashTool().execute(
            {"command": f'{_stdout("OUT")}; {_stderr("ERR")}'}, ctx
        )
        assert "[stderr]" in result.content
        assert "OUT" in result.content
        assert "ERR" in result.content
        # 顺序：stdout 在前，stderr 段在后
        assert result.content.index("OUT") < result.content.index("[stderr]")

    async def test_no_stderr_marker_when_stderr_empty(self, ctx):
        result = await BashTool().execute({"command": _stdout("only out")}, ctx)
        assert "[stderr]" not in result.content

    async def test_silent_command_reports_no_output(self, ctx):
        """模型据此区分「静默成功」与「输出被吞掉」。"""
        result = await BashTool().execute({"command": "exit 0"}, ctx)
        assert "(no output)" in result.content

    async def test_merge_streams_config_isolates_stderr(self, ctx):
        """merge_streams=True 退回旧行为：两条流合并，不加 [stderr] 标记。"""
        tool = BashTool(config=ShellConfig(merge_streams=True))
        result = await tool.execute({"command": f'{_stdout("OUT")}; {_stderr("ERR")}'}, ctx)
        assert "[stderr]" not in result.content
        assert "OUT" in result.content and "ERR" in result.content


class TestSpill:
    async def test_large_output_is_spilled_and_path_reported(self, ctx):
        """超限输出落盘，路径必须出现在 content 里（模型要靠它去读全文）。"""
        limit = 2000
        tool = BashTool(config=ShellConfig(max_output_bytes=limit))
        result = await tool.execute({"command": _py("print('x' * 20000)")}, ctx)
        assert "full output:" in result.content
        spilled = Path(_spill_path_from(result.content))
        assert spilled.exists()
        # 落盘的是「被环形缓冲挤出去的那部分」，完整输出 = 文件 + 内存尾部。
        # 断言这一点是为了证明「内容没丢，只是没全进上下文」。
        total = len(spilled.read_bytes()) + limit
        assert total >= 20000, f"落盘 {spilled.stat().st_size} + 内存 {limit} < 20000"

    async def test_small_output_is_not_spilled(self, ctx):
        result = await BashTool().execute({"command": _stdout("small")}, ctx)
        assert "full output:" not in result.content
        assert "truncated" not in result.content

    async def test_truncation_notice_is_the_last_line(self, ctx):
        """截断提示必须落在正文尾部。

        模型上下文侧还有一层 20k token 预算（pipeline/tool_result_budget.py），
        只保留头 500 / 尾 200 token —— 提示若不在尾部会被二次截断吃掉。
        """
        tool = BashTool(config=ShellConfig(max_output_bytes=1000))
        result = await tool.execute({"command": _py("print('y' * 50000)")}, ctx)
        assert "full output:" in result.content
        assert "full output:" in result.content.strip().splitlines()[-1]

    async def test_spill_lands_under_session_dir(self, ctx):
        """落盘位置复用会话目录，因此自动继承 conftest 的测试隔离。"""
        from nono_agent.session.transcript import active_session_dir

        tool = BashTool(config=ShellConfig(max_output_bytes=100))
        result = await tool.execute({"command": _py("print('z' * 5000)")}, ctx)
        assert active_session_dir() in Path(_spill_path_from(result.content)).parents


class TestWorkdir:
    async def test_relative_workdir_resolves_against_workspace(self, ctx, tmp_path):
        (tmp_path / "sub").mkdir()
        result = await BashTool().execute(
            {"command": _py("import os; print(os.path.basename(os.getcwd()))"),
             "workdir": "sub"},
            ctx,
        )
        assert "sub" in result.content

    async def test_escaping_workdir_is_rejected(self, ctx):
        """路径沙箱仍然生效 —— workdir 不能逃出工作区。"""
        from nono_agent.permissions.sandbox import PathEscapesSandboxError

        with pytest.raises(PathEscapesSandboxError):
            await BashTool().execute(
                {"command": "echo x", "workdir": "../../../"}, ctx
            )


class TestConfig:
    def test_timeout_is_clamped_to_max(self):
        cfg = ShellConfig(timeout_s=10, max_timeout_s=30)
        assert cfg.clamp_timeout(100) == 30
        assert cfg.clamp_timeout(5) == 5
        assert cfg.clamp_timeout(None) == 10
        assert cfg.clamp_timeout(-1) == 10  # 非法值回落到默认

    async def test_timeout_promotes_to_background(self, ctx):
        """默认：超时**不杀**命令，而是转后台作业交给模型跟进。"""
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(30)"), "timeout": 1}, ctx
        )
        assert result.is_error is False
        assert "moved to background job" in result.content
        assert "job_output" in result.content
        assert "job_kill" in result.content

    async def test_promotion_can_be_disabled(self, ctx):
        """关掉晋升即退回「超时即终止」，且仍是工具错误。"""
        tool = BashTool(config=ShellConfig(timeout_s=1, promote_on_timeout=False))
        result = await tool.execute({"command": _py("import time; time.sleep(30)")}, ctx)
        assert result.is_error
        assert "timed out" in result.content

    async def test_run_in_background_returns_job_id(self, ctx):
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(2)"), "run_in_background": True}, ctx
        )
        assert result.is_error is False
        assert "started background job job-" in result.content

    def test_rejects_non_positive_limits(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ShellConfig(max_output_bytes=0)
        with pytest.raises(ValidationError):
            ShellConfig(max_timeout_s=-1)

    def test_engine_config_carries_shell_section(self):
        from nono_agent.core.config import EngineConfig

        engine = EngineConfig(cwd=".", shell=ShellConfig(dialect="cmd"))
        assert engine.shell.dialect == "cmd"
        assert EngineConfig(cwd=".").shell.dialect == "auto"  # 有默认值

    async def test_cmd_dialect_escape_hatch(self, ctx):
        """dialect='cmd' 可退回改动前的 Windows 行为（Q2 的回退开关）。"""
        if not IS_WINDOWS:
            pytest.skip("cmd 方言是 Windows-only")
        tool = BashTool(config=ShellConfig(dialect="cmd"))
        result = await tool.execute({"command": "echo hello"}, ctx)
        assert "hello" in result.content


class TestEncoding:
    async def test_chinese_from_shell_itself(self, ctx):
        """编码前置的核心价值：PowerShell 自身输出与错误消息不再乱码。"""
        result = await BashTool().execute({"command": 'echo "中文测试"'}, ctx)
        assert "中文测试" in result.content
        assert "�" not in result.content

    async def test_chinese_from_child_process(self, ctx):
        result = await BashTool().execute({"command": _stdout("中文子进程")}, ctx)
        assert "中文子进程" in result.content
        assert "�" not in result.content

    async def test_error_message_is_readable(self, ctx):
        """PowerShell 错误消息随 $PSUICulture 变语言，因此只断言「无乱码」。"""
        result = await BashTool().execute(
            {"command": "Get-Item 'C:\\nope\\missing.txt'"}, ctx
        )
        assert "missing.txt" in result.content
        assert "�" not in result.content


class TestShellFeaturesPreserved:
    """去掉中间 shell 后，PowerShell / sh 自带的管道与重定向仍须可用。"""

    async def test_pipeline(self, ctx):
        result = await BashTool().execute(
            {"command": 'echo "a" | Select-Object -First 1' if IS_WINDOWS
             else 'echo "a" | head -1'},
            ctx,
        )
        assert "a" in result.content
        assert result.is_error is False

    async def test_redirection(self, ctx, tmp_path):
        """重定向仍可用 —— 证明去掉中间 shell 没有破坏语法。

        Windows PowerShell 5.1 的 ``>`` 走 Out-File，默认写 UTF-16LE，
        所以显式指定 utf8 才能按文本读回。
        """
        target = tmp_path / "out.txt"
        cmd = (
            f'"written" | Out-File -FilePath "{target}" -Encoding utf8'
            if IS_WINDOWS
            else f'echo "written" > "{target}"'
        )
        await BashTool().execute({"command": cmd}, ctx)
        assert target.read_text(encoding="utf-8-sig", errors="replace").strip() == "written"

    async def test_quotes_and_spaces_survive(self, ctx):
        """命令作为单个 argv 元素传入，不经中间 shell 的引号转义层。"""
        result = await BashTool().execute({"command": _stdout("a b  c")}, ctx)
        assert "a b  c" in result.content
