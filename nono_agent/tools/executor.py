"""工具执行引擎。

两种执行模式：
- StreamingToolExecutor: 流式执行，工具结果随完成而 yield（AsyncGenerator）
- BatchToolExecutor: 批量执行，收集所有结果后返回列表

并发策略：
- 并发安全工具 (is_concurrent_safe=True): asyncio.create_task 并行执行
- 非并发安全工具: 等并发工具完成后逐一顺序执行
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from nono_agent.core.types import ToolResult, ToolUseBlock
from nono_agent.observability.metrics import (
    METRIC_TOOL_ERRORS,
    METRIC_TOOL_EXECUTIONS,
    METRIC_TOOL_LATENCY,
    record_counter,
    record_histogram,
)
from nono_agent.permissions.policy import PermissionDecision
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.registry import ToolNotFound, ToolRegistry

# ---------------------------------------------------------------------------
# 工具执行结果包装器
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionResult:
    """包装工具执行结果 —— 包含原始结果和执行元数据。"""

    result: ToolResult               # 工具返回的原始结果
    tool_use_id: str                 # 工具调用 ID（对应 ToolUseBlock.id）
    tool_name: str                   # 工具名称
    duration_ms: float = 0.0         # 执行耗时（毫秒）
    was_cancelled: bool = False      # 是否因取消信号而跳过
    permission_denied: bool = False  # 是否因权限拒绝而跳过


# ---------------------------------------------------------------------------
# 流式执行器 —— 工具结果随完成而 yield
# ---------------------------------------------------------------------------


class StreamingToolExecutor:
    """流式工具执行器 —— 工具边流边执行，结果增量返回。

    并发策略：
    - 并发安全工具: asyncio.create_task 并行执行，asyncio.wait FIRST_COMPLETED
    - 非并发安全工具: 等所有并发工具完成后逐一执行

    使用方式:
        executor = StreamingToolExecutor(registry)
        async for result in executor.execute(tool_blocks, context):
            # 每个结果一完成就立即 yield
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @staticmethod
    def _cancelled(block_id: str, block_name: str) -> ToolExecutionResult:
        """被中止打断的工具结果。

        必须产出这么一条，而不是静默丢掉：模型下一轮会看到「调用了但没结果」，
        那是 API 会直接拒绝的悬空 tool_use。
        """
        return ToolExecutionResult(
            result=ToolResult(
                tool_use_id=block_id,
                content="Tool execution cancelled.",
                is_error=True,
                tool_name=block_name,
            ),
            tool_use_id=block_id,
            tool_name=block_name,
            was_cancelled=True,
        )

    @staticmethod
    async def _drain_cancelled(
        pending: set[asyncio.Task[ToolExecutionResult]],
        names: dict[str, str],
    ) -> AsyncGenerator[ToolExecutionResult, None]:
        """取消在跑的工具，逐条产出「被取消」结果。"""
        for task in pending:
            task.cancel()
        for task in pending:
            block_id = names.get(task.get_name(), "")
            with suppress(BaseException):
                await task
            block_name = names.get(block_id, "")
            yield StreamingToolExecutor._cancelled(block_id, block_name)

    async def execute(
        self,
        tool_blocks: list[ToolUseBlock],
        context: ToolUseContext,
    ) -> AsyncGenerator[ToolExecutionResult, None]:
        """执行工具块，结果随完成而 yield。

        并发安全工具并行执行，顺序工具逐一执行。

        两条路径都把「取结果」和「等中止」放进同一个 ``asyncio.wait``：工具执行
        期间同样可能挂很久（浏览器一次交互就几秒，MCP 更久），而绝大多数工具并不
        读 ``context.signal``。不race 的话，按停止要等**至少一个工具跑完**才生效，
        用户看到的就是「按钮没反应」—— 和流式输出那次一模一样的病根。
        """
        if not tool_blocks:
            return

        # 按并发安全性分割工具调用
        concurrent_blocks: list[ToolUseBlock] = []
        sequential_blocks: list[ToolUseBlock] = []

        for block in tool_blocks:
            tool = self._registry._tools.get(block.name)
            if tool is not None and tool.is_concurrent_safe:
                concurrent_blocks.append(block)
            else:
                sequential_blocks.append(block)

        signal = getattr(context, "signal", None)

        # 并发安全工具: asyncio.create_task 并行执行
        tasks: dict[str, asyncio.Task[ToolExecutionResult]] = {}
        names: dict[str, str] = {}
        for block in concurrent_blocks:
            name = f"tool:{block.name}:{block.id}"  # 任务名用于调试
            tasks[block.id] = asyncio.create_task(
                self._execute_one(block, context),
                name=name,
            )
            names[name] = block.id

        # 并发结果: FIRST_COMPLETED，完成一个就 yield 一个；中止随时打断。
        if tasks:
            pending = set(tasks.values())
            waiter = (
                asyncio.ensure_future(signal.wait()) if signal is not None else None
            )
            try:
                while pending:
                    if waiter is None:
                        done, pending = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_COMPLETED
                        )
                    else:
                        done, pending = await asyncio.wait(
                            pending | {waiter},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        # asyncio.wait 会把 waiter 一起算进返回值，这里摘掉。
                        pending = {t for t in pending if t is not waiter}
                        if waiter in done:
                            # 已经跑完的照常交结果，在跑的立刻停 —— 不等它们收尾。
                            for task in done:
                                if task is waiter:
                                    continue
                                yield await task
                            async for item in self._drain_cancelled(pending, names):
                                yield item
                            return
                    for task in done:
                        if task is waiter:
                            continue
                        result = await task
                        yield result
            finally:
                if waiter is not None:
                    waiter.cancel()

        # 顺序工具: 逐一执行；中止会中断当前这个，并且不再启动后面的。
        aborted = False
        for block in sequential_blocks:
            if aborted or (signal is not None and signal.aborted):
                aborted = True
                yield self._cancelled(block.id, block.name)
                continue

            task = asyncio.ensure_future(self._execute_one(block, context))
            waiter = (
                asyncio.ensure_future(signal.wait()) if signal is not None else None
            )
            try:
                if waiter is None:
                    yield await task
                    continue
                done, _ = await asyncio.wait(
                    {task, waiter}, return_when=asyncio.FIRST_COMPLETED
                )
                if waiter in done and task not in done:
                    task.cancel()
                    with suppress(BaseException):
                        await task
                    aborted = True
                    yield self._cancelled(block.id, block.name)
                    continue
                yield task.result()
            finally:
                if waiter is not None:
                    waiter.cancel()

    async def _execute_one(
        self,
        block: ToolUseBlock,
        context: ToolUseContext,
    ) -> ToolExecutionResult:
        """执行单个工具块 —— 完整的执行流程:
        1. 检查取消信号
        2. 查找工具
        3. 检查权限
        4. 执行工具
        5. 捕获异常
        """
        start = time.monotonic()  # 记录执行开始时间

        # 1. 检查取消信号
        if context.signal.aborted:
            return ToolExecutionResult(
                result=ToolResult(
                    tool_use_id=block.id,
                    content="Tool execution cancelled.",
                    is_error=True,
                    tool_name=block.name,
                ),
                tool_use_id=block.id,
                tool_name=block.name,
                was_cancelled=True,
            )

        # 2. 查找工具（未注册的工具返回 is_error=True）
        try:
            tool = self._registry.find(block.name)
        except ToolNotFound:
            return ToolExecutionResult(
                result=ToolResult(
                    tool_use_id=block.id,
                    content=f"Error: Tool '{block.name}' not found.",
                    is_error=True,
                    tool_name=block.name,
                ),
                tool_use_id=block.id,
                tool_name=block.name,
            )

        # 3. 检查权限（fail-closed：有策略时始终决策，否则仅写副作用工具询问）
        try:
            if context.policy is not None:
                decision = await context.check_permission_decision(
                    block.name,
                    block.input,
                    has_write_side_effects=tool.has_write_side_effects,
                )
            elif tool.should_prompt_for_permission(block.input, context):
                decision = await context.check_permission_decision(
                    block.name,
                    block.input,
                    has_write_side_effects=tool.has_write_side_effects,
                )
            else:
                decision = PermissionDecision.ALLOW
        except (EOFError, KeyboardInterrupt):
            # 用户在权限提示中按了 Ctrl+C / Ctrl+D → 视为拒绝
            decision = PermissionDecision.DENY

        if decision is not PermissionDecision.ALLOW:
            context.record_denial(
                block.name,
                f"Permission denied for tool '{block.name}'.",
                block.input,
            )
            return ToolExecutionResult(
                result=ToolResult(
                    tool_use_id=block.id,
                    content=f"Permission denied for tool '{block.name}'.",
                    is_error=True,
                    tool_name=block.name,
                ),
                tool_use_id=block.id,
                tool_name=block.name,
                permission_denied=True,
            )

        # 3.5 权限等待期间可能触发了 abort（Ctrl+C），重新检查
        if context.signal.aborted:
            return ToolExecutionResult(
                result=ToolResult(
                    tool_use_id=block.id,
                    content="Tool execution cancelled.",
                    is_error=True,
                    tool_name=block.name,
                ),
                tool_use_id=block.id,
                tool_name=block.name,
                was_cancelled=True,
            )

        # 4. 执行工具
        try:
            result = await tool.execute(block.input, context)
            elapsed = (time.monotonic() - start) * 1000  # 计算执行耗时
            record_counter(METRIC_TOOL_EXECUTIONS, tool=block.name)
            record_histogram(METRIC_TOOL_LATENCY, elapsed, tool=block.name)
            if result.is_error:
                record_counter(METRIC_TOOL_ERRORS, tool=block.name)
            return ToolExecutionResult(
                result=result,
                tool_use_id=block.id,
                tool_name=block.name,
                duration_ms=elapsed,
            )
        except Exception as exc:
            # 5. 捕获异常: 将异常信息作为 is_error 结果返回（不抛出）
            elapsed = (time.monotonic() - start) * 1000
            record_counter(METRIC_TOOL_EXECUTIONS, tool=block.name)
            record_counter(METRIC_TOOL_ERRORS, tool=block.name)
            return ToolExecutionResult(
                result=ToolResult(
                    tool_use_id=block.id,
                    content=f"Error executing tool '{block.name}': {exc}",
                    is_error=True,
                    tool_name=block.name,
                ),
                tool_use_id=block.id,
                tool_name=block.name,
                duration_ms=elapsed,
            )


# ---------------------------------------------------------------------------
# 批量执行器 —— 收集所有结果后返回列表
# ---------------------------------------------------------------------------


class BatchToolExecutor:
    """批量工具执行器 —— 内部复用 StreamingToolExecutor，收集所有结果。

    适合需要一次性获取所有结果的场景（如恢复链、测试）。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._streaming = StreamingToolExecutor(registry)

    async def execute(
        self,
        tool_blocks: list[ToolUseBlock],
        context: ToolUseContext,
    ) -> list[ToolExecutionResult]:
        """执行所有工具块，返回结果列表（按完成顺序）。"""
        results: list[ToolExecutionResult] = []
        async for result in self._streaming.execute(tool_blocks, context):
            results.append(result)
        return results

    async def execute_and_format(
        self,
        tool_blocks: list[ToolUseBlock],
        context: ToolUseContext,
    ) -> list[dict[str, Any]]:
        """执行并格式化为 tool_result 内容块。

        输出格式符合内部消息内容块协议：
        [{"type": "tool_result", "tool_use_id": ..., "content": ..., "is_error": ...}]
        """
        results = await self.execute(tool_blocks, context)
        return [
            {
                "type": "tool_result",
                "tool_use_id": r.tool_use_id,
                "content": r.result.content,
                "is_error": r.result.is_error,
            }
            for r in results
        ]
