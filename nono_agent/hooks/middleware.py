"""引擎中间件 —— 统一的 agentic loop 生命周期钩子。

替代 TS 版分散的 hook 调用点，用单个有序链实现。
中间件可以拦截和修改：
- API 调用前/后 (on_before_api_call / on_after_api_call)
- 状态转移 (on_transition) —— 支持否决
- 工具执行结果 (on_tool_result)
- 循环终止 (on_terminal)
- 异常处理 (on_error)

所有钩子均为 async，可选实现。MiddlewareChain 按注册顺序执行。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from nono_agent.core.state import LoopState, Terminal, Transition
from nono_agent.core.types import AssistantMessage, Message, ToolResult

logger = logging.getLogger(__name__)


@runtime_checkable
class EngineMiddleware(Protocol):
    """引擎生命周期中间件协议。

    所有钩子可选实现，按注册顺序调用。
    提供 MiddlewareBase 便利基类（默认 no-op）。
    """

    @property
    def name(self) -> str:
        """中间件唯一标识。"""
        ...

    async def on_before_api_call(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        state: LoopState,
    ) -> tuple[list[Message], str, list[dict[str, Any]]]:
        """API 调用前钩子 —— 可修改消息、系统提示、工具定义。"""
        ...

    async def on_after_api_call(
        self,
        *,
        assistant_message: AssistantMessage,
        state: LoopState,
    ) -> AssistantMessage:
        """API 响应组装后钩子 —— 可检查或修改 assistant 消息。"""
        ...

    async def on_tool_result(
        self,
        *,
        tool_name: str,
        tool_use_id: str,
        result: ToolResult,
        state: LoopState,
    ) -> ToolResult:
        """工具执行完成后钩子 —— 可检查或修改工具结果。"""
        ...

    async def on_transition(
        self,
        *,
        transition: Transition,
        state: LoopState,
    ) -> Transition | None:
        """状态转移钩子 —— 循环决定继续时调用。

        返回 None 表示否决转移（强制终止循环）。
        """
        ...

    async def on_terminal(
        self,
        *,
        terminal: Terminal,
        state: LoopState,
    ) -> Terminal:
        """循环终止钩子 —— 可检查或修改终止原因。"""
        ...

    async def on_error(
        self,
        *,
        error: Exception,
        phase: str,
        state: LoopState,
    ) -> bool:
        """异常处理钩子 —— 返回 True 表示已处理（继续执行）。"""
        ...


class MiddlewareBase:
    """中间件便利基类 —— 所有钩子默认 no-op，子类按需覆盖。"""

    _name: str = "base"

    @property
    def name(self) -> str:
        return self._name

    async def on_before_api_call(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        state: LoopState,
    ) -> tuple[list[Message], str, list[dict[str, Any]]]:
        return messages, system_prompt, tools

    async def on_after_api_call(
        self,
        *,
        assistant_message: AssistantMessage,
        state: LoopState,
    ) -> AssistantMessage:
        return assistant_message

    async def on_tool_result(
        self,
        *,
        tool_name: str,
        tool_use_id: str,
        result: ToolResult,
        state: LoopState,
    ) -> ToolResult:
        return result

    async def on_transition(
        self,
        *,
        transition: Transition,
        state: LoopState,
    ) -> Transition | None:
        return transition

    async def on_terminal(
        self,
        *,
        terminal: Terminal,
        state: LoopState,
    ) -> Terminal:
        return terminal

    async def on_error(
        self,
        *,
        error: Exception,
        phase: str,
        state: LoopState,
    ) -> bool:
        return False


class MiddlewareChain:
    """Ordered chain of EngineMiddleware instances.

    Usage:
        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        chain.add(PermissionMiddleware())

        # In the loop:
        messages, sys, tools = await chain.run_before_api_call(...)
    """

    def __init__(self) -> None:
        self._middlewares: list[EngineMiddleware] = []

    def add(self, middleware: EngineMiddleware) -> None:
        """Append a middleware to the chain."""
        self._middlewares.append(middleware)

    def remove(self, name: str) -> EngineMiddleware | None:
        """Remove a middleware by name."""
        for i, mw in enumerate(self._middlewares):
            if mw.name == name:
                return self._middlewares.pop(i)
        return None

    async def run_before_api_call(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        state: LoopState,
    ) -> tuple[list[Message], str, list[dict[str, Any]]]:
        """Run on_before_api_call through all middleware in order."""
        for mw in self._middlewares:
            try:
                messages, system_prompt, tools = await mw.on_before_api_call(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    state=state,
                )
            except Exception:
                logger.exception("Middleware '%s' on_before_api_call failed", mw.name)
        return messages, system_prompt, tools

    async def run_after_api_call(
        self,
        *,
        assistant_message: AssistantMessage,
        state: LoopState,
    ) -> AssistantMessage:
        """Run on_after_api_call through all middleware in order."""
        for mw in self._middlewares:
            try:
                assistant_message = await mw.on_after_api_call(
                    assistant_message=assistant_message,
                    state=state,
                )
            except Exception:
                logger.exception("Middleware '%s' on_after_api_call failed", mw.name)
        return assistant_message

    async def run_on_tool_result(
        self,
        *,
        tool_name: str,
        tool_use_id: str,
        result: ToolResult,
        state: LoopState,
    ) -> ToolResult:
        """Run on_tool_result through all middleware in order."""
        for mw in self._middlewares:
            try:
                result = await mw.on_tool_result(
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    result=result,
                    state=state,
                )
            except Exception:
                logger.exception("Middleware '%s' on_tool_result failed", mw.name)
        return result

    async def run_on_transition(
        self,
        *,
        transition: Transition,
        state: LoopState,
    ) -> Transition | None:
        """Run on_transition through all middleware. Returns None if any middleware vetoes."""
        for mw in self._middlewares:
            try:
                transition = await mw.on_transition(
                    transition=transition,
                    state=state,
                )
                if transition is None:
                    return None  # Vetoed
            except Exception:
                logger.exception("Middleware '%s' on_transition failed", mw.name)
        return transition

    async def run_on_terminal(
        self,
        *,
        terminal: Terminal,
        state: LoopState,
    ) -> Terminal:
        """Run on_terminal through all middleware in order."""
        for mw in self._middlewares:
            try:
                terminal = await mw.on_terminal(
                    terminal=terminal,
                    state=state,
                )
            except Exception:
                logger.exception("Middleware '%s' on_terminal failed", mw.name)
        return terminal

    async def run_on_error(
        self,
        *,
        error: Exception,
        phase: str,
        state: LoopState,
    ) -> bool:
        """Run on_error through all middleware. Returns True if any handled it."""
        for mw in self._middlewares:
            try:
                handled = await mw.on_error(
                    error=error,
                    phase=phase,
                    state=state,
                )
                if handled:
                    return True
            except Exception:
                logger.exception("Middleware '%s' on_error failed", mw.name)
        return False

    @property
    def middlewares(self) -> list[EngineMiddleware]:
        return list(self._middlewares)

    def __len__(self) -> int:
        return len(self._middlewares)
