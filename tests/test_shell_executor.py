"""ShellExecutor 能力协议与本地执行器的测试。

重点是**语义契约**（docs/Shell-Tool-Design.md §5.1），不是内部实现：
只有非零退出/超时/abort 都 resolve、on_expiry 二分正确、输出非消费式，
未来的沙箱执行器才敢继承这个类。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nono_agent.shell.dialect import resolve_dialect
from nono_agent.shell.local import LocalShellExecutor
from nono_agent.shell.types import (
    ON_EXPIRY_KILL,
    ON_EXPIRY_NONE,
    ShellExecutor,
    ShellProcess,
    ShellSpec,
)

IS_WINDOWS = sys.platform == "win32"
CALL = "&" if IS_WINDOWS else ""


def _py(snippet: str) -> str:
    return f'{CALL} "{sys.executable}" -c "{snippet}"'


def _spec(command: str, tmp_path: Path, **kw) -> ShellSpec:
    return ShellSpec(
        command=command,
        dialect=resolve_dialect("auto"),
        workdir=tmp_path,
        spill_dir=tmp_path / "spill",
        **kw,
    )


@pytest.fixture
def executor() -> LocalShellExecutor:
    return LocalShellExecutor()


class TestProtocolConformance:
    def test_local_executor_satisfies_protocol(self, executor):
        assert isinstance(executor, ShellExecutor)
        assert executor.sandbox_mode is None  # 本地执行器不沙箱

    async def test_handle_satisfies_protocol(self, executor, tmp_path):
        handle = await executor.execute(_spec(_py("print(1)"), tmp_path))
        assert isinstance(handle, ShellProcess)
        await handle.wait()

    async def test_argv_is_the_extension_point(self, tmp_path):
        """子类覆写 argv 即可改写执行方式 —— 沙箱执行器的接入形态。"""
        class Wrapped(LocalShellExecutor):
            def __init__(self) -> None:
                super().__init__()
                self.called = False

            def argv(self, spec: ShellSpec) -> list[str]:
                self.called = True
                return [sys.executable, "-c", "print('wrapped')"]

        wrapped = Wrapped()
        outcome = await (await wrapped.execute(_spec("ignored", tmp_path))).wait()
        assert "wrapped" in outcome.stdout.text
        assert wrapped.called

    async def test_build_env_is_the_other_extension_point(self, tmp_path):
        class WithEnv(LocalShellExecutor):
            def build_env(self, spec: ShellSpec) -> dict[str, str]:
                return {**super().build_env(spec), "SPILL_TEST_MARKER": "1"}

        outcome = await (await WithEnv().execute(
            _spec(_py("import os; print(os.environ['SPILL_TEST_MARKER'])"), tmp_path)
        )).wait()
        assert "1" in outcome.stdout.text


class TestOutcomeSemantics:
    async def test_nonzero_exit_resolves_not_raises(self, executor, tmp_path):
        outcome = await (await executor.execute(_spec("exit 3", tmp_path))).wait()
        assert outcome.exit_code == 3
        assert outcome.timed_out is False
        assert outcome.aborted is False
        assert outcome.status == "failed"

    async def test_clean_exit(self, executor, tmp_path):
        outcome = await (await executor.execute(_spec(_py("print('hi')"), tmp_path))).wait()
        assert outcome.exit_code == 0
        assert outcome.status == "completed"
        assert "hi" in outcome.stdout.text

    async def test_spawn_failure_raises(self, executor, tmp_path):
        """唯一允许 raise 的情形：基础设施故障（进程起不来）。"""
        spec = ShellSpec(
            command="echo x",
            dialect=resolve_dialect("auto"),
            workdir=tmp_path / "no-such-dir",
        )
        handle = await executor.execute(spec)
        assert handle.status == "killed"
        with pytest.raises(OSError):
            await handle.wait()

    async def test_unknown_dialect_is_rejected_early(self):
        """非法方言在构造 spec 时就报错，不留到 spawn。"""
        with pytest.raises(ValueError, match="unknown shell dialect"):
            resolve_dialect("no-such-shell")

    async def test_timeout_resolves_with_flag(self, executor, tmp_path):
        handle = await executor.execute(
            _spec(_py("import time; time.sleep(30)"), tmp_path,
                  timeout_s=1, on_expiry=ON_EXPIRY_KILL)
        )
        outcome = await handle.wait()
        assert outcome.timed_out is True
        assert outcome.status == "timed_out"

    async def test_timeout_is_prompt(self, executor, tmp_path):
        """超时后必须快速返回，不等孙进程自然结束。"""
        import time

        start = time.monotonic()
        handle = await executor.execute(
            _spec(_py("import time; time.sleep(30)"), tmp_path,
                  timeout_s=1, on_expiry=ON_EXPIRY_KILL)
        )
        await handle.wait()
        assert time.monotonic() - start < 10

    async def test_streams_are_separate(self, executor, tmp_path):
        outcome = await (await executor.execute(
            _spec(_py("import sys; sys.stdout.write('O'); sys.stderr.write('E')"), tmp_path)
        )).wait()
        assert "O" in outcome.stdout.text
        assert "E" in outcome.stderr.text
        assert "E" not in outcome.stdout.text


class TestOnExpiry:
    async def test_none_arms_no_deadline(self, executor, tmp_path):
        """on_expiry='none' 时执行器不设 deadline —— 晋升后台的前提。"""
        import asyncio

        handle = await executor.execute(
            _spec(_py("import time; time.sleep(2)"), tmp_path, on_expiry=ON_EXPIRY_NONE)
        )
        # 模拟工具层只等一小会儿就放手
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(asyncio.create_task(handle.wait())), timeout=0.3)
        # 句柄在调用方停止等待后依然有效 —— 这正是「晋升」需要的性质
        assert handle.status == "running"
        outcome = await handle.wait()
        assert outcome.timed_out is False
        assert outcome.status == "completed"

    async def test_kill_after_caller_stops_waiting(self, executor, tmp_path):
        handle = await executor.execute(
            _spec(_py("import time; time.sleep(30)"), tmp_path, on_expiry=ON_EXPIRY_NONE)
        )
        await handle.kill()
        outcome = await handle.wait()
        assert outcome.status == "killed"


class TestNonConsumingReads:
    async def test_repeated_reads_do_not_consume(self, executor, tmp_path):
        """执行中反复读同一偏移不丢数据 —— 作业注册表的实时推送靠这个。"""
        import asyncio

        handle = await executor.execute(
            _spec(_py("import time,sys; sys.stdout.write('A'); time.sleep(0.6); "
                      "sys.stdout.write('B')"), tmp_path, on_expiry=ON_EXPIRY_NONE)
        )
        await asyncio.sleep(0.2)
        first = handle.streams.deltas()[0].text
        again = handle.streams.deltas()[0].text
        assert first == again  # 同一偏移，结果一致
        await handle.wait()
        assert handle.streams.deltas()[0].text == "AB"  # 最终完整

    async def test_offsets_are_monotonic(self, executor, tmp_path):
        handle = await executor.execute(
            _spec(_py("print('x'*5000)"), tmp_path, max_output_bytes=1000)
        )
        await handle.wait()
        stdout = handle.streams.stdout
        first = stdout.read_from(0)
        assert len(first.text) == 1000  # 只给内存里保留的尾部
        assert first.spill_path is not None
        # 读到末尾后游标不再前进，且与新起点一致 —— 增量读的契约
        second = stdout.read_from(first.next_offset)
        assert second.text == ""
        assert second.next_offset == first.next_offset
        assert second.start_offset == first.next_offset


class TestAbort:
    async def test_abort_kills_process(self, tmp_path):
        from nono_agent.utils.abort import AbortController

        controller = AbortController()
        executor = LocalShellExecutor()
        handle = await executor.execute(
            _spec(_py("import time; time.sleep(30)"), tmp_path, on_expiry=ON_EXPIRY_NONE),
            signal=controller.signal,
        )
        controller.abort()
        outcome = await handle.wait()
        assert outcome.aborted is True
        assert outcome.status == "killed"
