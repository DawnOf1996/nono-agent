"""权限模式与会话策略 —— 对标 Claude Code PermissionMode。

四种会话模式（docs-site/docs/roadmap/index.md §2.6）：

- ``default``      写/执行需确认（ask），读自动允许
- ``accept_edits`` 与 ``default`` 当前行为一致（写仍需确认）；"工作区内编辑自动允许"
  由 rules 层表达，见 ``_mode_default`` 的说明
- ``plan``         禁止任何写副作用（只读）
- ``bypass``       显式危险模式，全部允许（deny 规则仍生效）

PermissionPolicy 组合「模式 + 可落盘规则」，产出三态决策：
``ALLOW`` / ``ASK`` / ``DENY``。规则优先级：**deny > ask > allow > 模式默认**。

两套规则清单，语义不同，不要混：

- ``rules``       —— 主规则表。「这个操作本身有多危险」。
- ``auto_approve`` —— 「不再询问」白名单。用户显式授权过的命令/路径，
  命中即放行，且**可以覆盖 ask 规则**（否则用户授权过的东西永远还在问，
  白名单就没意义了）。但它覆盖不了 deny、工具的绝对否决、安全底线与 plan 模式。

具体的拦截顺序在 ``permissions/gate.py`` —— 本模块只负责"匹配"，不负责"排序"。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from nono_agent.permissions.decision import PermissionDecision
from nono_agent.permissions.rules import PermissionRule, RuleAction


class PermissionMode(StrEnum):
    """会话权限模式 —— 决定未命中显式规则时的默认行为。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS = "bypass"


class PermissionPolicy:
    """会话权限策略 —— 模式 + 有序规则。

    规则匹配顺序：先 deny（命中即拒绝），再 ask，再 allow；
    全部未命中时回落到模式默认值。
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        rules: list[PermissionRule] | None = None,
        *,
        auto_approve: list[PermissionRule] | None = None,
    ) -> None:
        self.mode = mode
        self._pending_mode: PermissionMode | None = None
        self._rules: list[PermissionRule] = list(rules) if rules else []
        self._auto_approve: list[PermissionRule] = list(auto_approve) if auto_approve else []
        # 回调是**多订阅**的 —— 父会话与子 Agent 可能共用同一个策略对象，
        # 单槽位会让后注册的那个把前一个顶掉。
        self._on_mode_change: list[Callable[[PermissionMode], None]] = []
        self._on_mode_request: list[Callable[[PermissionMode, PermissionMode], None]] = []
        #: 进入 plan 之前的模式 —— 退出 plan 时恢复它，而不是拍脑袋回 default。
        self._pre_plan_mode: PermissionMode | None = None

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------

    def add_rule(self, rule: PermissionRule) -> None:
        """追加一条规则。"""
        self._rules.append(rule)

    def add_rules(self, rules: list[PermissionRule]) -> None:
        """批量追加规则。"""
        self._rules.extend(rules)

    def add_auto_approve(self, rule: PermissionRule) -> None:
        """追加一条「不再询问」白名单条目。"""
        self._auto_approve.append(rule)

    @property
    def rules(self) -> list[PermissionRule]:
        return list(self._rules)

    @property
    def auto_approve_rules(self) -> list[PermissionRule]:
        """「不再询问」白名单 —— 命中即放行，可覆盖 ask 规则。"""
        return list(self._auto_approve)

    @property
    def pending_mode(self) -> PermissionMode | None:
        """已请求、尚未在下一 turn/step 生效的模式。"""
        return self._pending_mode

    def set_mode(self, mode: PermissionMode) -> None:
        """立刻切换会话模式（恢复 header、测试、以及 ``commit_pending``）。"""
        self._pending_mode = None
        self.mode = mode
        for callback in list(self._on_mode_change):
            callback(mode)

    def request_mode(self, mode: PermissionMode) -> bool:
        """请求切换模式：立刻记 onchange，运行时仍用当前模式，直到 ``commit_pending``。

        Returns:
            是否产生了一次新的切换请求（同值重复请求返回 False）。
        """
        previous = self._pending_mode if self._pending_mode is not None else self.mode
        if mode is previous:
            return False
        self._pending_mode = None if mode is self.mode else mode
        # 记住进入 plan 之前的模式 —— 退出 plan 时恢复它。
        if mode is PermissionMode.PLAN:
            if self._pre_plan_mode is None and self.mode is not PermissionMode.PLAN:
                self._pre_plan_mode = self.mode
        for callback in list(self._on_mode_request):
            callback(previous, mode)
        return True

    @property
    def pre_plan_mode(self) -> PermissionMode:
        """进入 plan 之前的模式。没有进过 plan 就是 default。"""
        return self._pre_plan_mode or PermissionMode.DEFAULT

    def commit_pending(self) -> tuple[PermissionMode, PermissionMode] | None:
        """把挂起的模式应用到运行时。返回 ``(from, to)``；没有挂起则 ``None``。"""
        if self._pending_mode is None:
            return None
        from_mode = self.mode
        to_mode = self._pending_mode
        self._pending_mode = None
        if to_mode is from_mode:
            return None
        self.set_mode(to_mode)
        return (from_mode, to_mode)

    def on_mode_change(self, callback: Callable[[PermissionMode], None] | None) -> None:
        """注册模式真正生效时的回调（引擎用来把值写进 SessionHeader）。

        多订阅 —— 同一个回调重复注册只算一次。
        """
        if callback is None:
            return
        if callback not in self._on_mode_change:
            self._on_mode_change.append(callback)

    def on_mode_request(
        self, callback: Callable[[PermissionMode, PermissionMode], None] | None
    ) -> None:
        """注册模式被请求时的回调（``from`` → ``to``，引擎用来写 ``permission/onchange``）。

        多订阅 —— 同一个回调重复注册只算一次。
        """
        if callback is None:
            return
        if callback not in self._on_mode_request:
            self._on_mode_request.append(callback)

    # ------------------------------------------------------------------
    # 分层匹配 —— 拦截链按层调用
    # ------------------------------------------------------------------

    def match_deny(
        self, tool_name: str, params: dict[str, Any]
    ) -> PermissionRule | None:
        """第一条命中的 deny 规则。"""
        return self._first(self._rules, RuleAction.DENY, tool_name, params)

    def match_ask_tool_level(self, tool_name: str) -> PermissionRule | None:
        """工具级 ask 规则 —— 只声明了 tool_name 轴的。

        「整个工具都要问」与「这类操作要问」是两种东西：前者不能被
        「不再询问」白名单覆盖，后者可以。
        """
        for rule in self._rules:
            if rule.action is RuleAction.ASK and rule.is_tool_level:
                if rule.matches(tool_name, {}):
                    return rule
        return None

    def match_ask_content(
        self, tool_name: str, params: dict[str, Any]
    ) -> PermissionRule | None:
        """内容级 ask 规则 —— 声明了 path / bash_pattern 轴的。"""
        for rule in self._rules:
            if rule.action is RuleAction.ASK and rule.is_content_level:
                if rule.matches(tool_name, params):
                    return rule
        return None

    def match_ask(
        self, tool_name: str, params: dict[str, Any]
    ) -> PermissionRule | None:
        """任意形状的 ask 规则 —— 工具级优先。"""
        return self.match_ask_tool_level(tool_name) or self.match_ask_content(
            tool_name, params
        )

    def match_allow(
        self, tool_name: str, params: dict[str, Any]
    ) -> PermissionRule | None:
        """第一条命中的 allow 规则。"""
        return self._first(self._rules, RuleAction.ALLOW, tool_name, params)

    def match_auto_approve(
        self, tool_name: str, params: dict[str, Any]
    ) -> PermissionRule | None:
        """「不再询问」白名单命中。"""
        return self._first(self._auto_approve, RuleAction.ALLOW, tool_name, params)

    @staticmethod
    def _first(
        rules: list[PermissionRule],
        action: RuleAction,
        tool_name: str,
        params: dict[str, Any],
    ) -> PermissionRule | None:
        for rule in rules:
            if rule.action is action and rule.matches(tool_name, params):
                return rule
        return None

    # ------------------------------------------------------------------
    # 决策
    # ------------------------------------------------------------------

    def check(
        self,
        tool_name: str,
        input_params: dict[str, Any] | None = None,
        *,
        has_write_side_effects: bool = True,
    ) -> PermissionDecision:
        """对一次工具调用做权限决策。

        兼容旧接口 —— 与拦截链同源。优先级 **deny > ask > allow > 模式默认**。

        注意：这条路径不认「不再询问」白名单，也不做工具自评与安全底线检查 ——
        完整决策走 ``permissions/gate.py`` 的 ``PermissionGate``。

        Args:
            tool_name: 工具名（如 ``write_file``、``bash``）
            input_params: 工具输入参数（用于 path/bash 规则匹配）
            has_write_side_effects: 工具是否声明写副作用（决定模式默认行为）
        """
        params = input_params or {}

        # 1. deny 规则优先 —— 即使 bypass 模式也尊重显式 deny
        if self.match_deny(tool_name, params) is not None:
            return PermissionDecision.DENY

        # 2. ask 规则 —— 高于 allow：用户明写"这个要问"就不该被一条
        #    allow 规则盖掉。
        if self.match_ask(tool_name, params) is not None:
            return PermissionDecision.ASK

        # 3. allow 规则
        if self.match_allow(tool_name, params) is not None:
            return PermissionDecision.ALLOW

        # 4. 模式默认
        return self._mode_default(has_write_side_effects)

    def _mode_default(self, has_write_side_effects: bool) -> PermissionDecision:
        """按模式决定未命中规则时的默认行为。"""
        match self.mode:
            case PermissionMode.BYPASS:
                return PermissionDecision.ALLOW
            case PermissionMode.PLAN:
                # plan 模式禁止写副作用，读自动允许
                return (
                    PermissionDecision.DENY
                    if has_write_side_effects
                    else PermissionDecision.ALLOW
                )
            case PermissionMode.ACCEPT_EDITS:
                # 编辑类工具在工作区内的自动允许由 rules 层表达；
                # 此模式默认：读允许，写仍确认
                return (
                    PermissionDecision.ASK
                    if has_write_side_effects
                    else PermissionDecision.ALLOW
                )
            case PermissionMode.DEFAULT:
                return (
                    PermissionDecision.ASK
                    if has_write_side_effects
                    else PermissionDecision.ALLOW
                )


def policy_from_dict(data: dict[str, Any]) -> PermissionPolicy:
    """从配置字典构建 PermissionPolicy（用于落盘配置加载）。

    期望格式::

        {
            "mode": "default",           # default | accept_edits | plan | bypass
            "rules": [
                {"action": "allow", "tool_name": "read_file", "path": "src/**"},
                {"action": "deny", "tool_name": "bash", "bash_pattern": "rm -rf"},
            ],
            "auto_approve": [
                {"action": "allow", "tool_name": "bash", "bash_pattern": "^git status"},
            ],
        }

    ``auto_approve`` 是「不再询问」白名单 —— 命中即放行，且可以覆盖 ask 规则。
    """
    mode_raw = data.get("mode", "default")
    try:
        mode = PermissionMode(mode_raw)
    except ValueError:
        mode = PermissionMode.DEFAULT

    def _rules_from(items: Any) -> list[PermissionRule]:
        rules: list[PermissionRule] = []
        if not isinstance(items, list):
            return rules
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                rules.append(PermissionRule.from_dict(item))
            except (KeyError, ValueError):
                continue
        return rules

    rules = _rules_from(data.get("rules", []))
    auto_approve = _rules_from(data.get("auto_approve", []))

    return PermissionPolicy(mode=mode, rules=rules, auto_approve=auto_approve)


def policy_from_settings(settings: Any) -> PermissionPolicy:
    """从合并后的 Settings 读取 ``permissions`` 段并构建 PermissionPolicy。

    未配置 ``permissions`` 时返回默认策略。
    """
    data = settings.get("permissions", {}) if settings is not None else {}
    if not isinstance(data, dict):
        data = {}
    return policy_from_dict(data)
