"""工具执行上下文。

ToolUseContext 携带工具执行所需的全部信息：
工作目录、取消信号、权限检查器、额外上下文等。
使用 frozen dataclass 防止工具篡改共享状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from nono_agent.utils.abort import AbortSignal


# 权限决策回调: (tool_name, input_params) -> bool | Awaitable[bool]
# 支持同步和异步检查器（如需要查询数据库的异步权限检查）
PermissionChecker = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]


@dataclass(frozen=True)  # 不可变，防止工具篡改共享状态
class ToolUseContext:
    """工具执行上下文 —— 传递给每个 tool.execute() 调用的不可变上下文。"""

    # 文件操作的工作目录
    cwd: str

    # 取消信号 —— 工具应定期检查 signal.aborted 以支持及时取消
    signal: AbortSignal = field(default_factory=AbortSignal)

    # 会话 ID，用于多会话隔离
    session_id: str = ""

    # 权限检查器 —— 在写操作前调用，支持同步/异步
    permission_checker: PermissionChecker | None = None

    # 中间件或引擎注入的额外上下文
    extra: dict[str, Any] = field(default_factory=dict)

    async def check_permission(
        self,
        tool_name: str,
        input_params: dict[str, Any],
    ) -> bool:
        """询问权限检查器是否允许此次工具调用。

        返回 True=允许, False=拒绝。
        未设置 permission_checker 时默认允许（宽松模式）。
        支持同步和异步检查器。
        """
        if self.permission_checker is None:
            return True  # 无检查器时默认允许

        result = self.permission_checker(tool_name, input_params)
        # 兼容异步检查器（如查询远程权限服务）
        if isinstance(result, Awaitable):
            return await result
        return result

    def with_extra(self, **kwargs: Any) -> ToolUseContext:
        """返回带额外 extra 上下文的副本（不可变，构造新实例）。"""
        merged = {**self.extra, **kwargs}
        return ToolUseContext(
            cwd=self.cwd,
            signal=self.signal,
            session_id=self.session_id,
            permission_checker=self.permission_checker,
            extra=merged,
        )
