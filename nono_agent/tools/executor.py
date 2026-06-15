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
from dataclasses import dataclass, field
from typing import Any

from nono_agent.core.types import ToolResult, ToolUseBlock
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

    async def execute(
        self,
        tool_blocks: list[ToolUseBlock],
        context: ToolUseContext,
    ) -> AsyncGenerator[ToolExecutionResult, None]:
        """执行工具块，结果随完成而 yield。

        并发安全工具并行执行，顺序工具逐一执行。
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

        # 并发安全工具: asyncio.create_task 并行执行
        tasks: dict[str, asyncio.Task[ToolExecutionResult]] = {}
        for block in concurrent_blocks:
            task = asyncio.create_task(
                self._execute_one(block, context),
                name=f"tool:{block.name}:{block.id}",  # 任务名用于调试
            )
            tasks[block.id] = task

        # 并发结果: asyncio.wait FIRST_COMPLETED，完成一个就 yield 一个
        if tasks:
            pending = set(tasks.values())
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    result = await task
                    yield result
                # abort 后取消剩余任务，避免已取消的查询仍在后台执行工具
                if context.signal.aborted:
                    for t in pending:
                        t.cancel()
                    # 等待取消完成，收集已取消的结果
                    for t in pending:
                        try:
                            result = await t
                            yield result
                        except asyncio.CancelledError:
                            # 从 task name 提取 block id
                            name = t.get_name()  # tool:{name}:{id}
                            parts = name.split(":")
                            block_id = parts[2] if len(parts) >= 3 else ""
                            block_name = parts[1] if len(parts) >= 2 else ""
                            yield ToolExecutionResult(
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
                    break  # 退出 while pending

        # 顺序工具: 逐一执行，每个结果检查取消信号
        for block in sequential_blocks:
            if context.signal.aborted:
                yield ToolExecutionResult(
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
                continue

            result = await self._execute_one(block, context)
            yield result

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

        # 3. 检查权限（有写副作用的工具需要用户授权）
        needs_permission = tool.should_prompt_for_permission(block.input, context)
        if needs_permission:
            try:
                allowed = await context.check_permission(block.name, block.input)
            except (EOFError, KeyboardInterrupt):
                # 用户在权限提示中按了 Ctrl+C / Ctrl+D → 视为拒绝
                allowed = False
            if not allowed:
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
            return ToolExecutionResult(
                result=result,
                tool_use_id=block.id,
                tool_name=block.name,
                duration_ms=elapsed,
            )
        except Exception as exc:
            # 5. 捕获异常: 将异常信息作为 is_error 结果返回（不抛出）
            elapsed = (time.monotonic() - start) * 1000
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
