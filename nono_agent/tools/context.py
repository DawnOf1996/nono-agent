"""工具执行上下文。

ToolUseContext 携带工具执行所需的全部信息：
工作目录、取消信号、权限检查器、权限策略、路径沙箱、拒绝追踪等。
使用 frozen dataclass 防止工具篡改共享状态。

权限决策链路（fail-closed）：
1. 若注入 ``policy``（PermissionPolicy）→ 三态决策 ALLOW/ASK/DENY
2. ASK 或未注入 policy 时 → 回落到 ``permission_checker`` 交互回调（bool）
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nono_agent.permissions.policy import PermissionDecision, PermissionPolicy
from nono_agent.permissions.sandbox import PathSandbox
from nono_agent.utils.abort import AbortSignal

# 权限决策回调: (tool_name, input_params) -> bool | Awaitable[bool]
# 支持同步和异步检查器（如需要查询数据库的异步权限检查）
PermissionChecker = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]

# AskUserQuestion 回调: (questions) -> dict | Awaitable[dict]
# questions: [{"question": str, "header": str, "options": [...], "multi_select": bool}]
# 返回: {"answers": {question_text_or_id: selected_label | [labels]}}
AskUserCallback = Callable[[list[dict[str, Any]]], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(frozen=True)  # 不可变，防止工具篡改共享状态
class ToolUseContext:
    """工具执行上下文 —— 传递给每个 tool.execute() 调用的不可变上下文。"""

    # 文件操作的工作目录
    cwd: str

    # 取消信号 —— 工具应定期检查 signal.aborted 以支持及时取消
    signal: AbortSignal = field(default_factory=AbortSignal)

    # 会话 ID，用于多会话隔离
    session_id: str = ""

    # 权限检查器 —— 交互式回调（CLI 询问 / Server 拒绝），支持同步/异步
    permission_checker: PermissionChecker | None = None

    # 权限策略（PermissionPolicy）—— 注入后启用三态规则决策
    policy: PermissionPolicy | None = None

    # 路径沙箱 —— 注入后工具按此校验路径逃逸
    sandbox: PathSandbox | None = None

    # 拒绝追踪器 —— 权限拒绝记录回灌 SDKResult.permission_denials
    denial_tracker: Any | None = None  # DenialTracker

    # Ignore 匹配器 —— 工具在列举/搜索时跳过被忽略路径
    ignore: Any | None = None  # IgnoreMatcher

    # AskUserQuestion 回调 —— 人在环结构化提问（CLI 交互 / Server 回调）
    ask_user: AskUserCallback | None = None

    # 工具注册表 —— 供 ToolSearch 等工具按需检索
    tool_registry: Any | None = None  # ToolRegistry

    # 中间件或引擎注入的额外上下文
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 权限决策
    # ------------------------------------------------------------------

    async def check_permission_decision(
        self,
        tool_name: str,
        input_params: dict[str, Any],
        *,
        has_write_side_effects: bool = True,
    ) -> PermissionDecision:
        """按策略做三态决策；未注入 policy 时回落到布尔检查器。

        返回 ALLOW / ASK / DENY。
        """
        if self.policy is not None:
            decision = self.policy.check(
                tool_name,
                input_params,
                has_write_side_effects=has_write_side_effects,
            )
            if decision is not PermissionDecision.ASK:
                return decision
            # ASK → 交给交互检查器解析
            allowed = await self.check_permission(tool_name, input_params)
            return PermissionDecision.ALLOW if allowed else PermissionDecision.DENY

        # 无 policy：沿用旧布尔语义
        allowed = await self.check_permission(tool_name, input_params)
        return PermissionDecision.ALLOW if allowed else PermissionDecision.DENY

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

    def record_denial(
        self,
        tool_name: str,
        reason: str,
        input_params: dict[str, Any] | None = None,
    ) -> None:
        """记录一次权限拒绝（若注入了拒绝追踪器）。"""
        if self.denial_tracker is not None:
            mode = self.policy.mode.value if self.policy is not None else "default"
            self.denial_tracker.record(
                tool_name, reason, input_params, mode=mode
            )

    # ------------------------------------------------------------------
    # 路径沙箱
    # ------------------------------------------------------------------

    async def ask_user_question(
        self,
        questions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """发起结构化提问并返回答案。

        未设置 ask_user 回调时返回 None（通道不可用）。
        支持同步和异步回调。
        """
        if self.ask_user is None:
            return None
        result = self.ask_user(questions)
        if isinstance(result, Awaitable):
            return await result
        return result

    def resolve_path(self, path: str) -> str:
        """解析路径 —— 若注入 sandbox 则校验逃逸，否则简单基于 cwd 拼接。"""
        import os

        if self.sandbox is not None:
            return str(self.sandbox.validate(path))
        if os.path.isabs(path):
            return path
        return os.path.join(self.cwd, path)

    def with_extra(self, **kwargs: Any) -> ToolUseContext:
        """返回带额外 extra 上下文的副本（不可变，构造新实例）。"""
        merged = {**self.extra, **kwargs}
        return ToolUseContext(
            cwd=self.cwd,
            signal=self.signal,
            session_id=self.session_id,
            permission_checker=self.permission_checker,
            policy=self.policy,
            sandbox=self.sandbox,
            denial_tracker=self.denial_tracker,
            ignore=self.ignore,
            ask_user=self.ask_user,
            tool_registry=self.tool_registry,
            extra=merged,
        )
