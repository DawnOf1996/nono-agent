"""Post-sampling 钩子 —— LLM 响应采样后、工具执行前运行。

提供扩展点：
- 工具调用摘要（异步通过小模型生成）
- 响应校验/脱敏
- 指标采集
- 侧通道通知
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from nono_agent.core.types import AssistantMessage, ToolUseBlock

logger = logging.getLogger(__name__)


@runtime_checkable
class PostSamplingHook(Protocol):
    """采样后钩子协议 —— LLM 响应完整采样后、工具执行前调用。"""

    @property
    def name(self) -> str:
        """Hook 唯一标识。"""
        ...

    async def on_sampled(
        self,
        assistant_message: AssistantMessage,
        tool_blocks: list[ToolUseBlock],
        context: dict[str, Any],
    ) -> AssistantMessage:
        """LLM 响应采样完成后调用 —— 可观察、修改响应或触发副作用。"""
        ...


class PostSamplingChain:
    """Chain of post-sampling hooks executed in order.

    Usage:
        chain = PostSamplingChain()
        chain.add(ToolUseSummaryHook(summary_model="claude-haiku"))
        chain.add(MetricsHook())

        msg = await chain.run(assistant_msg, tool_blocks, ctx)
    """

    def __init__(self) -> None:
        self._hooks: list[PostSamplingHook] = []

    def add(self, hook: PostSamplingHook) -> None:
        """Add a post-sampling hook."""
        self._hooks.append(hook)

    def remove(self, name: str) -> PostSamplingHook | None:
        """Remove a hook by name."""
        for i, h in enumerate(self._hooks):
            if h.name == name:
                return self._hooks.pop(i)
        return None

    async def run(
        self,
        assistant_message: AssistantMessage,
        tool_blocks: list[ToolUseBlock],
        context: dict[str, Any],
    ) -> AssistantMessage:
        """Execute all hooks in order, threading the message through."""
        for hook in self._hooks:
            try:
                assistant_message = await hook.on_sampled(
                    assistant_message, tool_blocks, context
                )
            except Exception:
                logger.exception(
                    "Post-sampling hook '%s' failed, continuing", hook.name
                )
        return assistant_message

    def __len__(self) -> int:
        return len(self._hooks)


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------


class ToolUseSummaryHook:
    """Launches an async task to generate summaries for tool executions.

    The summary generation runs as a background asyncio task.
    The summary is attached to the message store in the next loop iteration.
    """

    def __init__(
        self,
        summary_model: str = "",
        min_tools_for_summary: int = 3,
    ) -> None:
        self._model = summary_model
        self._min_tools = min_tools_for_summary
        self._pending_tasks: list[asyncio.Task[str]] = []

    @property
    def name(self) -> str:
        return "tool_use_summary"

    async def on_sampled(
        self,
        assistant_message: AssistantMessage,
        tool_blocks: list[ToolUseBlock],
        context: dict[str, Any],
    ) -> AssistantMessage:
        if len(tool_blocks) < self._min_tools:
            return assistant_message

        # In a real implementation, this would launch an async task
        # to call a smaller model for summarization.
        # For now, we just log the intent.
        tool_names = [b.name for b in tool_blocks]
        logger.debug(
            "ToolUseSummary: would summarize %d tools: %s",
            len(tool_blocks),
            tool_names,
        )

        return assistant_message

    def collect_pending_summaries(self) -> list[str]:
        """Collect completed summary tasks."""
        summaries: list[str] = []
        remaining: list[asyncio.Task[str]] = []
        for task in self._pending_tasks:
            if task.done():
                try:
                    summaries.append(task.result())
                except Exception:
                    pass
            else:
                remaining.append(task)
        self._pending_tasks = remaining
        return summaries
