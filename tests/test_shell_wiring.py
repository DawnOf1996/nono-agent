"""冒烟：三条装配路径（库 / CLI / Server）都能拿到带 shell 能力的工具集。"""

from __future__ import annotations

import asyncio
import sys

import pytest

from nono_agent.core.config import EngineConfig, ShellConfig
from nono_agent.shell.jobs import JobRegistry
from nono_agent.shell.local import LocalShellExecutor
from nono_agent.tools.builtin import BashTool, JobKillTool, JobOutputTool, create_builtin_tools
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.registry import ToolRegistry


def _find(tools, name):
    return next(t for t in tools if t.name == name)


class TestLibraryUsage:
    def test_zero_arg_still_works(self):
        """零参调用保持兼容（既有调用点不必改）。"""
        names = {t.name for t in create_builtin_tools()}
        assert {"bash", "job_output", "job_kill"} <= names

    def test_config_flows_into_bash(self):
        tools = create_builtin_tools(EngineConfig(cwd=".", shell=ShellConfig(dialect="cmd")))
        bash = _find(tools, "bash")
        assert isinstance(bash, BashTool)
        assert bash.shell_config.dialect == "cmd"
        assert "cmd.exe" in bash.description

    def test_description_advertises_dialect(self):
        """模型必须知道命令要用哪种 shell 语法，否则会写错。"""
        posix = BashTool(config=ShellConfig(dialect="sh")).description
        assert "PowerShell" not in posix
        ps = BashTool(config=ShellConfig(dialect="powershell")).description
        assert "PowerShell" in ps
        assert "Get-ChildItem" in ps  # 给出正确的语法提示
        assert "call operator" in ps


class TestContextInjection:
    def test_with_extra_preserves_shell_fields(self):
        """with_extra 手工逐字段复制 —— 漏掉 shell/shell_jobs 会让子代理失效。"""
        from dataclasses import replace

        registry = JobRegistry("s")
        ctx = ToolUseContext(
            cwd=".", session_id="s",
            shell=LocalShellExecutor(), shell_jobs=registry,
        )
        derived = ctx.with_extra(marker=1)
        assert derived.shell is ctx.shell
        assert derived.shell_jobs is registry
        assert derived.extra["marker"] == 1
        assert replace(ctx, session_id="t").shell_jobs is registry

    async def test_three_tools_share_one_registry(self, tmp_path):
        """bash 登记的作业必须能被 job_output / job_kill 看到。"""
        registry = JobRegistry("s")
        ctx = ToolUseContext(
            cwd=str(tmp_path), session_id="s",
            shell=LocalShellExecutor(), shell_jobs=registry,
        )
        call = "&" if sys.platform == "win32" else ""
        result = await BashTool().execute(
            {
                "command": f'{call} "{sys.executable}" -c "import time; time.sleep(10)"',
                "run_in_background": True,
            },
            ctx,
        )
        job_id = next(t for t in result.content.split() if t.startswith("job-"))
        assert registry.get(job_id) is not None
        assert (await JobOutputTool().execute({"job_id": job_id}, ctx)).is_error is False
        assert (await JobKillTool().execute({"job_id": job_id}, ctx)).is_error is False
        await registry.kill_all()


class TestRegistryFallback:
    async def test_without_injected_registry_falls_back(self, tmp_path):
        """库直接调工具（不经过引擎）时退回按会话分片的门面。"""
        from nono_agent.jobs import board

        board.reset_jobs()
        ctx = ToolUseContext(cwd=str(tmp_path), session_id="fallback-session")
        call = "&" if sys.platform == "win32" else ""
        result = await BashTool().execute(
            {
                "command": f'{call} "{sys.executable}" -c "import time; time.sleep(5)"',
                "run_in_background": True,
            },
            ctx,
        )
        job_id = next(t for t in result.content.split() if t.startswith("job-"))
        assert board.registry_for("fallback-session").get(job_id) is not None
        assert len(board.list_jobs("fallback-session")) == 1
        await board.registry_for("fallback-session").kill_all()


class TestServerWhitelist:
    def test_job_kill_allowed_in_unattended_mode(self):
        """server 模式无人值守，但停自己的进程不该被拒。"""
        pytest.importorskip("fastapi")
        from nono_app_server.engine.session_manager import deny_all_permission_checker

        assert deny_all_permission_checker("job_kill", {"job_id": "job-1"}) is True
        assert deny_all_permission_checker("write_file", {"path": "/x"}) is False

    def test_job_output_never_reaches_the_checker(self):
        """只读工具不需要授权，因此不经过 deny-all 检查器。

        调用链见 tools/executor.py：只有 should_prompt_for_permission 为真
        （即有写副作用）时才会询问检查器。
        """
        assert JobOutputTool().should_prompt_for_permission({"job_id": "j"}, None) is False
        assert JobKillTool().should_prompt_for_permission({"job_id": "j"}, None) is True


class TestServerSessionRegistry:
    def test_server_registry_accepts_config(self):
        pytest.importorskip("fastapi")
        from nono_app_server.engine.session_manager import build_session_tool_registry

        registry: ToolRegistry = build_session_tool_registry(
            EngineConfig(cwd=".", shell=ShellConfig(dialect="sh"))
        )
        assert registry.find("bash").shell_config.dialect == "sh"  # type: ignore[attr-defined]
        assert registry.find("job_output") is not None


class TestEngineWiring:
    async def test_engine_injects_shell_and_jobs(self):
        """引擎构造时必须建好执行器与注册表，并进 ToolUseContext。"""
        from nono_agent.core.engine import QueryEngine

        engine = QueryEngine(EngineConfig(cwd="."))
        try:
            assert isinstance(engine._shell, LocalShellExecutor)
            assert isinstance(engine._shell_jobs, JobRegistry)
        finally:
            await engine.close()

    async def test_engine_close_kills_background_jobs(self):
        from nono_agent.core.engine import QueryEngine

        engine = QueryEngine(EngineConfig(cwd="."))
        registry = engine._shell_jobs
        ctx = ToolUseContext(
            cwd=".", session_id="e", signal=engine._abort_controller.signal,
            shell=engine._shell, shell_jobs=registry,
        )
        call = "&" if sys.platform == "win32" else ""
        result = await BashTool().execute(
            {
                "command": f'{call} "{sys.executable}" -c "import time; time.sleep(30)"',
                "run_in_background": True,
            },
            ctx,
        )
        job_id = next(t for t in result.content.split() if t.startswith("job-"))
        assert registry.get(job_id) is not None
        assert registry.running_count() == 1

        await engine.close()
        assert registry.running_count() == 0, "引擎关闭后不应留下孤儿进程"
        assert asyncio.iscoroutinefunction(QueryEngine.close)
