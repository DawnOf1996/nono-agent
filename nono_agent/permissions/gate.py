"""权限拦截链 —— **顺序即优先级**。

模型说「我要执行这个命令」，从这句话到命令真的跑起来，中间隔着这道检查。
这里就是那道检查。

链的顺序写死在代码里，而不是散在各层或写在文档里 —— 顺序只有一处定义，
才谈得上"不可能被绕过"。

分四段（段的划分本身就是优先级）::

    绝对收紧层    任何自动放行都越不过
      0   HOOK_PRE         hook 否决          → DENY
      1a  RULE_DENY        显式禁止规则       → DENY
      1c  TOOL_VERDICT     工具的绝对否决     → DENY
      1e  TOOL_ALWAYS_ASK  工具声明必须人工   → ASK
      1g  SAFETY_BASELINE  安全底线           → DENY / ASK
      1h  PLAN_MODE        plan 模式只读      → DENY

    用户标记层    可被「不再询问」白名单部分覆盖
      1b  RULE_ASK 工具级  「这个工具每次都问」 → ASK
      2a  AUTO_APPROVE     「这些命令不再问」   → ALLOW
      1f  RULE_ASK 内容级  「这类操作要问」     → ASK

    放宽层
      2b  MODE_BYPASS      全部放行模式       → ALLOW
      2c  RULE_ALLOW       总是允许规则       → ALLOW

    兜底层
      3   DEFAULT          没被声明就当要问   → ASK / ALLOW

从顺序里直接读出三条性质：

1. **禁止永远赢** —— deny 在最前，bypass 也绕不过。
2. **人的显式配置高于省事模式** —— 用户明写的 ask / 危险标记排在 bypass 之前。
3. **有些边界连用户自己都不能放开** —— 安全底线与工具的绝对否决排在
   一切自动放行之前。

「软 ASK / 硬 ASK」不靠标志位区分，**靠位置**：收紧层的 ASK 谁都动不了，
兜底层的 ASK 可被 bypass 放宽。这样"哪种 ASK 免疫哪种模式"是结构读出来的。

每一步都是一个独立的 :class:`PermissionCheck` 协议实现 —— 判据各不相同
（规则 / 工具声明 / 路径底线 / 模式），塞进一个基类只会得到一个到处是 if
的巨类。加一步就是加一个类 + 往链里放一个实例。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from nono_agent.permissions.baseline import BaselineSeverity, SafetyBaseline
from nono_agent.permissions.decision import PermissionDecision
from nono_agent.permissions.policy import PermissionMode, PermissionPolicy

logger = logging.getLogger(__name__)


class GateStep(StrEnum):
    """拦截链的步骤 —— 命中哪一步要记进审计。"""

    HOOK_PRE = "hook_pre"
    RULE_DENY = "rule_deny"
    TOOL_VERDICT = "tool_verdict"
    TOOL_ALWAYS_ASK = "tool_always_ask"
    SAFETY_BASELINE = "safety_baseline"
    PLAN_MODE = "plan_mode"
    RULE_ASK = "rule_ask"
    AUTO_APPROVE = "auto_approve"
    RULE_ASK_CONTENT = "rule_ask_content"
    MODE_BYPASS = "mode_bypass"
    RULE_ALLOW = "rule_allow"
    DEFAULT = "default"


@dataclass(frozen=True)
class GateOutcome:
    """一次拦截链的结论。

    ``step`` 与 ``rule`` 一起进审计事件 —— 能看出「是哪一步拦的」和
    「被哪条规则拦的」，而不是只有一句"权限被拒绝"。
    """

    decision: PermissionDecision
    step: GateStep
    reason: str = ""
    rule: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is PermissionDecision.ALLOW


@dataclass(frozen=True)
class PermissionRequest:
    """送进链里的一次工具调用。

    ``tool_verdict`` 在链外算一次 —— 工具自评是纯函数，四处问同一个工具
    只会得到四个可能不一致的答案。

    ``hook_approved`` 表示"用户 hook 明确放行"—— 它让这次调用**不必再问**，
    但不改变链的其它判定：显式 deny 规则、工具的绝对否决、安全底线、plan 模式
    照样拦。hook 能加规则，不能拆规则。
    """

    tool: Any
    tool_name: str
    input_params: dict[str, Any]
    context: Any
    tool_verdict: PermissionDecision
    cwd: str
    hook_approved: bool = False


@runtime_checkable
class PermissionCheck(Protocol):
    """拦截链的一步。

    未命中返回 ``None``；命中返回终局结果。
    """

    step: GateStep

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        ...


# ---------------------------------------------------------------------------
# 工具自评 —— 链外算一次，供各步复用
# ---------------------------------------------------------------------------


def tool_verdict_of(
    tool: Any,
    input_params: dict[str, Any],
    context: Any,
) -> PermissionDecision:
    """取工具对本次调用的自评。

    拿不到 ``evaluate_permission`` 就退回静态的 ``has_write_side_effects``；
    两者都拿不到就当**有写副作用**（要问）。漏写声明的后果应该是"多问一次"，
    而不是"漏放一次"。
    """
    evaluator = getattr(tool, "evaluate_permission", None)
    if callable(evaluator):
        try:
            verdict = evaluator(input_params, context)
        except Exception:  # noqa: BLE001 —— 工具自评崩了不能变成放行
            logger.exception(
                "Tool %r failed to evaluate_permission — treating as write",
                getattr(tool, "name", tool),
            )
            return PermissionDecision.ASK
        if isinstance(verdict, PermissionDecision):
            return verdict
        # 返回 None（Protocol 占位实现）或别的类型 → 退回静态声明。

    getter = getattr(tool, "has_write_side_effects", None)
    if getter is not None:
        try:
            # 正常是 property（取值），兼容写成方法的实现。
            value = getter() if callable(getter) else getter
            return PermissionDecision.ASK if value else PermissionDecision.ALLOW
        except Exception:  # noqa: BLE001
            logger.exception("Tool %r failed has_write_side_effects", tool)
            return PermissionDecision.ASK
    return PermissionDecision.ASK


def _tool_always_requires_approval(tool: Any) -> bool:
    """工具是否声明必须人工确认。拿不到就当不需要。"""
    try:
        return bool(getattr(tool, "always_requires_approval", False))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 绝对收紧层
# ---------------------------------------------------------------------------


class HookPreCheck:
    """PreToolUse hook —— 串行前置。

    hook 与检查链**串联，不并联**：hook 说拒绝 → 拒绝；hook 说允许 →
    继续走完整链，不能跳过任何安全检查。业务方能加规则，但不能拆规则。
    """

    step = GateStep.HOOK_PRE

    def __init__(self, hooks: Any | None = None) -> None:
        self._hooks = hooks

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        if self._hooks is None:
            return None
        checker = getattr(self._hooks, "check_before_tool", None)
        if not callable(checker):
            return None

        try:
            verdict = await checker(
                req.tool_name,
                req.input_params,
                {
                    "cwd": req.cwd,
                    "session_id": getattr(req.context, "session_id", ""),
                    "mode": getattr(getattr(req.context, "policy", None), "mode", None),
                },
            )
        except Exception:  # noqa: BLE001 —— hook 崩了按拒绝处理，不按放行
            logger.exception("before_tool hook failed — treating as deny")
            return GateOutcome(
                PermissionDecision.DENY,
                self.step,
                "before_tool hook failed — failing closed.",
            )

        decision = getattr(verdict, "decision", None)
        if decision is None:
            # 兼容旧版二态 verdict
            decision = (
                PermissionDecision.ALLOW
                if getattr(verdict, "allowed", True)
                else PermissionDecision.DENY
            )

        if decision is PermissionDecision.DENY:
            return GateOutcome(
                PermissionDecision.DENY,
                self.step,
                getattr(verdict, "reason", None) or "vetoed by hook",
                getattr(verdict, "rule", None),
            )
        if decision is PermissionDecision.ASK:
            return GateOutcome(
                PermissionDecision.ASK,
                self.step,
                getattr(verdict, "reason", None) or "hook requires approval",
                getattr(verdict, "rule", None),
            )
        # ALLOW —— hook 明确放行：这次调用**不必再问**。
        # 但链继续往下走 —— deny 规则、安全底线、plan 模式照常查。
        return GateOutcome(
            PermissionDecision.ALLOW,
            self.step,
            "approved by before_tool hook.",
            getattr(verdict, "rule", None) or "hook:allow",
        )


class DenyRuleCheck:
    """显式禁止规则 —— 永远赢。"""

    step = GateStep.RULE_DENY

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        rule = self._policy.match_deny(req.tool_name, req.input_params)
        if rule is None:
            return None
        return GateOutcome(
            PermissionDecision.DENY,
            self.step,
            rule.description or f"deny rule '{rule.label}' matched.",
            rule.label,
        )


class ToolVerdictCheck:
    """工具自己的绝对否决 —— 只认 DENY。

    工具说 ASK / ALLOW 都**不短路**：链继续往下走。工具说"这次没事"不等于
    "可以跳过安全底线检查" —— 那正是"一条捷径绕过了另一条规则"。
    """

    step = GateStep.TOOL_VERDICT

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        if req.tool_verdict is not PermissionDecision.DENY:
            return None
        return GateOutcome(
            PermissionDecision.DENY,
            self.step,
            f"tool '{req.tool_name}' refused this call.",
            f"tool:{req.tool_name}",
        )


class ToolAlwaysAskCheck:
    """工具声明「必须人工确认」—— 任何模式都不能跳过。"""

    step = GateStep.TOOL_ALWAYS_ASK

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        if not _tool_always_requires_approval(req.tool):
            return None
        return GateOutcome(
            PermissionDecision.ASK,
            self.step,
            f"tool '{req.tool_name}' always requires human approval.",
            f"tool:{req.tool_name}:always-approval",
        )


class SafetyBaselineCheck:
    """安全底线 —— 连 bypass 模式都免疫。"""

    step = GateStep.SAFETY_BASELINE

    def __init__(self, baseline: SafetyBaseline) -> None:
        self._baseline = baseline

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        hit = self._baseline.check(req.tool_name, req.input_params, req.cwd)
        if hit is None:
            return None
        decision = (
            PermissionDecision.DENY
            if hit.severity is BaselineSeverity.DENY
            else PermissionDecision.ASK
        )
        return GateOutcome(decision, self.step, hit.reason, hit.rule)


class PlanModeCheck:
    """plan 模式 —— 只读，有写副作用的调用直接拒。

    判定用的是工具自评（按入参动态求值），所以 `ls` 这种被工具判为无写
    副作用的命令在 plan 模式下照常能跑。
    """

    step = GateStep.PLAN_MODE

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        if self._policy.mode is not PermissionMode.PLAN:
            return None
        if req.tool_verdict is not PermissionDecision.ASK:
            return None
        return GateOutcome(
            PermissionDecision.DENY,
            self.step,
            "plan mode is read-only — write side effects are denied.",
            "mode:plan",
        )


# ---------------------------------------------------------------------------
# 用户标记层
# ---------------------------------------------------------------------------


class AskRuleToolLevelCheck:
    """工具级 ask 规则 —— 「这个工具每次都问」。

    不可被「不再询问」白名单覆盖：用户明写了这个工具永远要问，
    白名单是另一回事。
    """

    step = GateStep.RULE_ASK

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        rule = self._policy.match_ask_tool_level(req.tool_name)
        if rule is None:
            return None
        return GateOutcome(
            PermissionDecision.ASK,
            self.step,
            rule.description or f"tool '{req.tool_name}' is marked always-ask.",
            rule.label,
        )


class AutoApproveCheck:
    """「不再询问」白名单 —— 用户显式授权过的操作直接放行。

    位置很讲究：它在绝对收紧层**之后**（越不过 deny / 工具否决 / 安全底线 /
    plan 模式），在内容级 ask 规则**之前**（用户授权过的具体命令不再问）。
    """

    step = GateStep.AUTO_APPROVE

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        rule = self._policy.match_auto_approve(req.tool_name, req.input_params)
        if rule is None:
            return None
        return GateOutcome(
            PermissionDecision.ALLOW,
            self.step,
            rule.description or "auto-approved by user.",
            rule.label,
        )


class AskRuleContentCheck:
    """内容级 ask 规则 —— 「这类操作要问」。

    在「不再询问」白名单之后：白名单命中就轮不到它了。
    """

    step = GateStep.RULE_ASK_CONTENT

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        rule = self._policy.match_ask_content(req.tool_name, req.input_params)
        if rule is None:
            return None
        return GateOutcome(
            PermissionDecision.ASK,
            self.step,
            rule.description or "content matched an ask rule.",
            rule.label,
        )


# ---------------------------------------------------------------------------
# 放宽层
# ---------------------------------------------------------------------------


class BypassModeCheck:
    """全部放行模式 —— 收紧层没拦住就放行。"""

    step = GateStep.MODE_BYPASS

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        if self._policy.mode is not PermissionMode.BYPASS:
            return None
        return GateOutcome(
            PermissionDecision.ALLOW,
            self.step,
            "bypass mode — all approvals skipped.",
            "mode:bypass",
        )


class AllowRuleCheck:
    """总是允许规则。"""

    step = GateStep.RULE_ALLOW

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        rule = self._policy.match_allow(req.tool_name, req.input_params)
        if rule is None:
            return None
        return GateOutcome(
            PermissionDecision.ALLOW,
            self.step,
            rule.description or f"allow rule '{rule.label}' matched.",
            rule.label,
        )


# ---------------------------------------------------------------------------
# 兜底层
# ---------------------------------------------------------------------------


class DefaultCheck:
    """兜底 —— 默认立场是**问**，不是放。

    工具没声明自己没写副作用，就当有写副作用。

    ``hook_approved`` 时直接放行：用户 hook 明确说了"这条不用问"，
    这正是无人值守 agent 唯一的放行口子。但它只影响**兜底**这一层 ——
    链上更靠前的收紧判定（deny 规则 / 工具否决 / 安全底线 / plan 模式）
    与用户明写的 ask 规则都已经生效了。
    """

    step = GateStep.DEFAULT

    async def evaluate(self, req: PermissionRequest) -> GateOutcome | None:
        if req.hook_approved:
            return GateOutcome(
                PermissionDecision.ALLOW,
                self.step,
                "approved by before_tool hook — no approval needed.",
                "hook:allow",
            )
        if req.tool_verdict is PermissionDecision.ALLOW:
            return GateOutcome(
                PermissionDecision.ALLOW,
                self.step,
                "tool declared no write side effects.",
                "tool:read-only",
            )
        return GateOutcome(
            PermissionDecision.ASK,
            self.step,
            "write side effect requires approval.",
            "default:ask",
        )


# ---------------------------------------------------------------------------
# 拦截链
# ---------------------------------------------------------------------------


@dataclass
class PermissionGate:
    """权限拦截链 —— 决策的唯一实现。

    链的顺序在这里定死了。别的模块（executor / context）只调用它，
    不自己判断权限 —— 判断散到两处就等于没有优先级可言。
    """

    policy: PermissionPolicy
    baseline: SafetyBaseline = field(default_factory=SafetyBaseline)
    hooks: Any | None = None

    def __post_init__(self) -> None:
        self._checks: tuple[PermissionCheck, ...] = (
            # ── 绝对收紧层 ──
            HookPreCheck(self.hooks),
            DenyRuleCheck(self.policy),
            ToolVerdictCheck(),
            ToolAlwaysAskCheck(),
            SafetyBaselineCheck(self.baseline),
            PlanModeCheck(self.policy),
            # ── 用户标记层 ──
            AskRuleToolLevelCheck(self.policy),
            AutoApproveCheck(self.policy),
            AskRuleContentCheck(self.policy),
            # ── 放宽层 ──
            BypassModeCheck(self.policy),
            AllowRuleCheck(self.policy),
            # ── 兜底层 ──
            DefaultCheck(),
        )

    @property
    def steps(self) -> tuple[str, ...]:
        """链上的步骤顺序 —— 供测试与文档断言用。"""
        return tuple(str(c.step) for c in self._checks)

    async def evaluate(
        self,
        tool: Any,
        tool_name: str,
        input_params: dict[str, Any],
        context: Any,
    ) -> GateOutcome:
        """跑一遍链，返回结论。

        Args:
            tool: 工具实例
            tool_name: 工具名
            input_params: 工具入参
            context: ToolUseContext
        """
        req = PermissionRequest(
            tool=tool,
            tool_name=tool_name,
            input_params=input_params or {},
            context=context,
            tool_verdict=tool_verdict_of(tool, input_params or {}, context),
            cwd=str(getattr(context, "cwd", "") or ""),
        )
        for check in self._checks:
            outcome = await check.evaluate(req)
            if outcome is None:
                continue
            if (
                outcome.step is GateStep.HOOK_PRE
                and outcome.decision is PermissionDecision.ALLOW
            ):
                # hook 明确放行 —— 标记一下，**继续走链**。
                # 底线、deny 规则、plan 模式都在后面，照常查。
                req = replace(req, hook_approved=True)
                continue
            return outcome
        # DefaultCheck 永远命中 —— 兜底就在链尾。
        return GateOutcome(
            PermissionDecision.ASK,
            GateStep.DEFAULT,
            "no rule matched — asking by default.",
        )
