"""job_output / job_kill 工具与作业注册表的测试。

重点验证三条契约（docs-site/docs/architecture/shell-tool.md §6.4）：

1. 晋升后进程**真的还活着**（超时不杀）
2. 增量读**不重复**
3. 晋升后用户中断**仍能杀掉后台进程**（abort 监听的所有权移交）
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from nono_agent.core.config import ShellConfig
from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
from nono_agent.shell.jobs import JobRegistry
from nono_agent.tools.builtin import BashTool, JobKillTool, JobOutputTool
from nono_agent.tools.context import ToolUseContext
from nono_agent.utils.abort import AbortController

IS_WINDOWS = sys.platform == "win32"
CALL = "&" if IS_WINDOWS else ""


def _py(snippet: str) -> str:
    return f'{CALL} "{sys.executable}" -c "{snippet}"'


@pytest.fixture
def registry() -> JobRegistry:
    return JobRegistry("test-session")


@pytest.fixture
def ctx(tmp_path, registry):
    from nono_agent.shell.local import LocalShellExecutor

    return ToolUseContext(
        cwd=str(tmp_path),
        sandbox=PathSandbox(tmp_path),
        ignore=IgnoreMatcher.from_workspace(tmp_path),
        session_id="test-session",
        shell=LocalShellExecutor(),
        shell_jobs=registry,
    )


def _job_id_from(content: str) -> str:
    for token in content.replace(":", " ").replace(",", " ").split():
        if token.startswith("job-"):
            return token.rstrip(".!]")
    raise AssertionError(f"no job id in {content!r}")


class TestPromotion:
    async def test_timeout_keeps_the_process_alive(self, ctx):
        """超时不杀命令 —— 这是晋升与「超时即终止」的本质区别。"""
        tool = BashTool(config=ShellConfig(timeout_s=1, promote_on_timeout=True))
        result = await tool.execute({"command": _py("import time; time.sleep(20)")}, ctx)
        assert result.is_error is False
        job_id = _job_id_from(result.content)

        await asyncio.sleep(0.3)
        job = ctx.shell_jobs.get(job_id)
        assert job is not None
        assert job.status == "running"

    async def test_promotion_returns_promptly(self, ctx):
        tool = BashTool(config=ShellConfig(timeout_s=1, promote_on_timeout=True))
        start = time.monotonic()
        await tool.execute({"command": _py("import time; time.sleep(20)")}, ctx)
        assert time.monotonic() - start < 8

    async def test_disabled_promotion_terminates(self, ctx):
        tool = BashTool(config=ShellConfig(timeout_s=1, promote_on_timeout=False))
        result = await tool.execute({"command": _py("import time; time.sleep(20)")}, ctx)
        assert result.is_error
        assert "timed out" in result.content

    async def test_run_in_background_flag(self, ctx):
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(3)"), "run_in_background": True}, ctx
        )
        assert "started background job job-" in result.content
        job_id = _job_id_from(result.content)
        assert ctx.shell_jobs.get(job_id).status == "running"


class TestJobOutput:
    async def _promote(self, ctx, snippet: str) -> str:
        result = await BashTool(config=ShellConfig(timeout_s=1)).execute(
            {"command": _py(snippet)}, ctx
        )
        return _job_id_from(result.content)

    async def test_incremental_reads_do_not_repeat(self, ctx):
        """晋升消息已消费过的输出，job_output 不会重复给。

        契约（对齐 DSH）：晋升时返回的文本就是「到目前为止的输出」，
        且**推进游标**；后续 job_output 只给更新的部分。
        """
        # flush 是必须的：管道下 Python 的 stdout 是块缓冲的
        job_id = await self._promote(
            ctx,
            "import sys,time; sys.stdout.write('A'); sys.stdout.flush(); time.sleep(3); "
            "sys.stdout.write('B'); sys.stdout.flush(); time.sleep(10)",
        )
        first = await JobOutputTool().execute({"job_id": job_id}, ctx)
        assert "A" not in first.content  # A 已在晋升消息里给过

        await asyncio.sleep(3)
        second = await JobOutputTool().execute({"job_id": job_id}, ctx)
        assert "B" in second.content

        third = await JobOutputTool().execute({"job_id": job_id}, ctx)
        assert "B" not in third.content  # 已消费完，不重复

    async def test_promoted_message_carries_early_output(self, ctx):
        """晋升那一刻已经产生的输出要出现在返回文本里（否则模型两眼一黑）。"""
        result = await BashTool(config=ShellConfig(timeout_s=1)).execute(
            {"command": _py("import sys,time; sys.stdout.write('EARLY'); "
                            "sys.stdout.flush(); time.sleep(20)")},
            ctx,
        )
        assert "EARLY" in result.content
        assert "moved to background job" in result.content

    async def test_status_is_reported(self, ctx):
        job_id = await self._promote(ctx, "import time; time.sleep(10)")
        result = await JobOutputTool().execute({"job_id": job_id}, ctx)
        assert f"[job {job_id} status: running]" in result.content

    async def test_unknown_job_lists_alternatives(self, ctx):
        result = await JobOutputTool().execute({"job_id": "job-does-not-exist"}, ctx)
        assert result.is_error
        assert "Unknown job" in result.content

    async def test_missing_job_id(self, ctx):
        result = await JobOutputTool().execute({}, ctx)
        assert result.is_error
        assert "job_id is required" in result.content

    async def test_from_offset_explicit_read(self, ctx):
        job_id = await self._promote(
            ctx,
            "import sys,time; sys.stdout.write('hello'); sys.stdout.flush(); time.sleep(10)",
        )
        await asyncio.sleep(0.3)
        result = await JobOutputTool().execute({"job_id": job_id, "from_offset": 0}, ctx)
        assert "hello" in result.content


class TestJobKill:
    async def test_kill_stops_the_process(self, ctx):
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(30)"), "run_in_background": True}, ctx
        )
        job_id = _job_id_from(result.content)
        killed = await JobKillTool().execute({"job_id": job_id, "reason": "test"}, ctx)
        assert killed.is_error is False
        assert f"Stopped job {job_id}" in killed.content
        assert ctx.shell_jobs.get(job_id).status == "killed"

    async def test_kill_is_idempotent(self, ctx):
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(30)"), "run_in_background": True}, ctx
        )
        job_id = _job_id_from(result.content)
        await JobKillTool().execute({"job_id": job_id}, ctx)
        again = await JobKillTool().execute({"job_id": job_id}, ctx)
        assert again.is_error is False
        assert "already killed" in again.content

    async def test_kill_unknown_job(self, ctx):
        result = await JobKillTool().execute({"job_id": "nope"}, ctx)
        assert result.is_error
        assert "Unknown job" in result.content

    async def test_kill_has_write_side_effects(self):
        """终止进程是状态变更，必须走权限（server 模式靠白名单放行）。"""
        assert JobKillTool().has_write_side_effects is True
        assert JobOutputTool().has_write_side_effects is False


class TestAbortHandover:
    async def test_abort_kills_promoted_job(self, tmp_path, registry):
        """**P1 最关键的一条**：晋升后 execute 已返回，用户中断仍要能杀掉进程。

        若 abort 监听没有移交给注册表，executor 的 task 随 execute 一起结束，
        没人再响应信号，晋升的进程会一直跑到自己结束。
        """
        controller = AbortController()
        from nono_agent.shell.local import LocalShellExecutor

        ctx = ToolUseContext(
            cwd=str(tmp_path),
            sandbox=PathSandbox(tmp_path),
            session_id="test-session",
            signal=controller.signal,
            shell=LocalShellExecutor(),
            shell_jobs=registry,
        )
        result = await BashTool(config=ShellConfig(timeout_s=1)).execute(
            {"command": _py("import time; time.sleep(30)")}, ctx
        )
        job_id = _job_id_from(result.content)
        assert registry.get(job_id).status == "running"

        controller.abort()
        for _ in range(50):
            if registry.get(job_id).status != "running":
                break
            await asyncio.sleep(0.1)
        assert registry.get(job_id).status == "killed"

    async def test_engine_close_kills_promoted_jobs(self, ctx):
        """引擎销毁必须收掉后台作业，否则留下孤儿进程。"""
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(30)"), "run_in_background": True}, ctx
        )
        job_id = _job_id_from(result.content)
        await ctx.shell_jobs.kill_all()
        assert ctx.shell_jobs.get(job_id).status == "killed"


class TestRegistryIsolation:
    async def test_registries_do_not_share_jobs(self, ctx):
        """两个会话的作业互不可见 —— 这正是实例化的目的。"""
        result = await BashTool().execute(
            {"command": _py("import time; time.sleep(5)"), "run_in_background": True}, ctx
        )
        job_id = _job_id_from(result.content)

        other = JobRegistry("other-session")
        assert other.get(job_id) is None
        assert other.list() == []

        from dataclasses import replace

        other_ctx = replace(ctx, shell_jobs=other)
        denied = await JobOutputTool().execute({"job_id": job_id}, other_ctx)
        assert denied.is_error
        assert "Unknown job" in denied.content
