"""工具执行期间按停止的及时性。

和流式输出那次同源的一个问题，也确实存在：``StreamingToolExecutor`` 在
``await asyncio.wait(pending, FIRST_COMPLETED)`` 和
``await self._execute_one(...)`` 上一直阻塞，中止判断排在阻塞之后 ——
所以按停止要等**至少一个工具跑完**才生效。一个跑 60 秒的工具，按钮就像没点。

谁会拖这么久？只有 ``bash`` 真的注册了 ``signal`` 监听（``shell/local.py``
的 ``_watch()`` 杀进程）。browser / read / edit / grep / lsp / MCP 都不看信号 ——
浏览器一次交互就是几百毫秒到几秒，叠加起来足够让人以为按钮失灵。

⚠️ 写这类测试时踩过的坑（同中止那次）：测试工具**不能自己检查 signal**。
自己检查的话，测到的是工具自己那道守卫，不是执行器的调度。下面的工具一律不看
信号，唯一该被测出来的是执行器什么时候「不再等」。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from nono_agent.core.types import ToolResult, ToolUseBlock
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.executor import StreamingToolExecutor
from nono_agent.tools.protocol import ToolBase
from nono_agent.tools.registry import ToolRegistry
from nono_agent.utils.abort import AbortController


class SlowTool(ToolBase):
    """跑很久、且**不响应** signal 的工具。

    刻意不看 ``context.signal.aborted`` —— 真实工具大多如此，唤醒的责任在执行器。

    Attributes:
        cancelled: 是否在完成前被取消（进程/任务有没有被真正收掉）。
        started: 是否真的开跑过。
    """

    def __init__(self, name: str, seconds: float, concurrent_safe: bool) -> None:
        self._name = name
        self._description = "慢工具"
        self._input_schema = {"type": "object", "properties": {}}
        self._is_concurrent_safe = concurrent_safe
        self._has_write_side_effects = False
        self.seconds = seconds
        self.cancelled = False
        self.started = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        self.started = True
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolResult(
            tool_use_id="",
            content="done",
            tool_name=self._name,
        )


def _block(block_id: str, name: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name=name, input={})


def _context(signal: Any) -> ToolUseContext:
    return ToolUseContext(cwd=".", session_id="s", signal=signal)


class TestAbortDuringToolExecution:
    """工具已经开跑时按停止。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("concurrent_safe", [True, False])
    async def test_abort_stops_waiting_promptly(self, concurrent_safe: bool):
        """按停止后不该再等工具跑完 —— 并发和顺序两条路径都要覆盖。

        两条路径原本都会卡满 ``seconds``：并发路径的判断排在
        ``asyncio.wait`` 之后，顺序路径压根没有中止后的取消。
        """
        controller = AbortController()
        tool = SlowTool("slow", seconds=30.0, concurrent_safe=concurrent_safe)
        registry = ToolRegistry()
        registry.register(tool)  # type: ignore[arg-type]
        executor = StreamingToolExecutor(registry)

        async def drive() -> list[Any]:
            return [
                item
                async for item in executor.execute(
                    [_block("t1", "slow")], _context(controller.signal)
                )
            ]

        task = asyncio.create_task(drive())
        # 等工具真的开跑，再按停止（固定 sleep 会在任务还没起来时就中止）
        deadline = time.monotonic() + 5.0
        while not tool.started and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        assert tool.started, "工具没开跑，测不到执行器什么时候不再等"

        started = time.monotonic()
        controller.abort()
        await task
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"按停止后仍等了 {elapsed:.2f}s —— 工具还在跑"
        assert tool.cancelled, "在跑的工具没被取消，会一直占着"

    @pytest.mark.asyncio
    async def test_cancelled_tool_is_reported_not_silently_dropped(self):
        """被取消的工具要有一条结果 —— 否则模型看到的是凭空少了一次调用。"""
        controller = AbortController()
        tool = SlowTool("slow", seconds=30.0, concurrent_safe=False)
        registry = ToolRegistry()
        registry.register(tool)  # type: ignore[arg-type]
        executor = StreamingToolExecutor(registry)

        async def drive() -> list[Any]:
            return [
                item
                async for item in executor.execute(
                    [_block("t1", "slow")], _context(controller.signal)
                )
            ]

        task = asyncio.create_task(drive())
        deadline = time.monotonic() + 5.0
        while not tool.started and time.monotonic() < deadline:
            await asyncio.sleep(0.005)

        controller.abort()
        results = await task

        assert results, "取消后一条结果都没有，模型会以为工具从没被调用"
        assert results[-1].was_cancelled is True
        assert results[-1].tool_use_id == "t1"

    @pytest.mark.asyncio
    async def test_remaining_tools_cancelled_after_first_abort(self):
        """顺序工具：前一个卡住时按停止，后面的不该再被启动。"""
        controller = AbortController()
        first = SlowTool("first", seconds=30.0, concurrent_safe=False)
        second = SlowTool("second", seconds=0.01, concurrent_safe=False)
        registry = ToolRegistry()
        registry.register(first)  # type: ignore[arg-type]
        registry.register(second)  # type: ignore[arg-type]
        executor = StreamingToolExecutor(registry)

        async def drive() -> list[Any]:
            return [
                item
                async for item in executor.execute(
                    [_block("t1", "first"), _block("t2", "second")],
                    _context(controller.signal),
                )
            ]

        task = asyncio.create_task(drive())
        deadline = time.monotonic() + 5.0
        while not first.started and time.monotonic() < deadline:
            await asyncio.sleep(0.005)

        controller.abort()
        results = await task

        assert first.cancelled is True
        assert second.started is False, "中止后还启动了下一个工具"
        assert len(results) == 2, "两个工具块都该有一条结果"

    @pytest.mark.asyncio
    async def test_no_abort_still_waits_and_returns_real_result(self):
        """对照组：不按停止时行为不能变。"""
        controller = AbortController()
        tool = SlowTool("slow", seconds=0.05, concurrent_safe=True)
        registry = ToolRegistry()
        registry.register(tool)  # type: ignore[arg-type]
        executor = StreamingToolExecutor(registry)

        results = [
            item
            async for item in executor.execute(
                [_block("t1", "slow")], _context(controller.signal)
            )
        ]

        assert len(results) == 1
        assert results[0].result.content == "done"
        assert results[0].was_cancelled is False
        assert tool.cancelled is False
