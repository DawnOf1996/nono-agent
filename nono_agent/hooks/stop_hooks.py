"""Stop hooks —— 校验/拦截循环继续的钩子。

在每个 assistant 响应后、工具执行前运行。
可以：
- 返回 blocking errors（注入用户反馈让 LLM 重试）
- 设置 prevent_continuation=True 完全阻止继续
- 返回空结果允许正常继续
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nono_agent.core.types import AssistantMessage

logger = logging.getLogger(__name__)


@dataclass
class StopHookResult:
    """Stop hook 结果 —— 包含可选的阻塞错误和拦截标志。"""

    # 非空时，这些错误消息作为用户反馈注入对话（让 LLM 修正）
    blocking_errors: list[str] = field(default_factory=list)

    # True 时循环立即终止
    prevent_continuation: bool = False

    # 可选元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        """是否有阻塞错误（可重试）。"""
        return bool(self.blocking_errors)

    @property
    def is_preventing(self) -> bool:
        """是否明确阻止继续。"""
        return self.prevent_continuation


@runtime_checkable
class StopHook(Protocol):
    """Stop hook 协议 —— 校验 assistant 响应并决定是否可以继续。"""

    @property
    def name(self) -> str:
        """Hook 唯一标识。"""
        ...

    async def check(
        self,
        assistant_message: AssistantMessage,
        context: dict[str, Any],
    ) -> StopHookResult:
        """检查 assistant 响应是否合法。"""
        ...


class StopHookExecutor:
    """执行所有已注册 stop hook 的执行器。"""

    def __init__(self) -> None:
        self._hooks: list[StopHook] = []

    def register(self, hook: StopHook) -> None:
        """注册 stop hook。"""
        self._hooks.append(hook)

    def unregister(self, name: str) -> StopHook | None:
        """按名称移除 hook。"""
        for i, h in enumerate(self._hooks):
            if h.name == name:
                return self._hooks.pop(i)
        return None

    async def execute(
        self,
        assistant_message: AssistantMessage,
        context: dict[str, Any],
    ) -> StopHookResult:
        """执行所有 hook 并合并结果 —— prevent 时短路。

        如果任何 hook 设置了 prevent_continuation=True，后续 hook 不再执行。
        所有 hook 的 blocking_errors 会被收集并合并。
        """
        all_errors: list[str] = []
        any_prevent = False
        merged_metadata: dict[str, Any] = {}

        for hook in self._hooks:
            try:
                result = await hook.check(assistant_message, context)

                all_errors.extend(result.blocking_errors)
                any_prevent = any_prevent or result.prevent_continuation
                merged_metadata.update(result.metadata)

                # prevent 时短路，不再执行后续 hook
                if any_prevent:
                    logger.info("Stop hook '%s' prevented continuation", hook.name)
                    break

            except Exception:
                # hook 异常不中断循环，记录日志后继续
                logger.exception("Stop hook '%s' failed, continuing", hook.name)

        return StopHookResult(
            blocking_errors=all_errors,
            prevent_continuation=any_prevent,
            metadata=merged_metadata,
        )

    @property
    def hooks(self) -> list[StopHook]:
        return list(self._hooks)

    def __len__(self) -> int:
        return len(self._hooks)
