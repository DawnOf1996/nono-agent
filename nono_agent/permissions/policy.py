"""权限模式与会话策略 —— 对标 Claude Code PermissionMode。

四种会话模式（docs/ROADMAP.md §2.6）：

- ``default``      写/执行需确认（ask），读自动允许
- ``accept_edits`` 工作区内编辑自动允许；其它写仍需确认
- ``plan``         禁止任何写副作用（只读）
- ``bypass``       显式危险模式，全部允许（deny 规则仍生效）

PermissionPolicy 组合「模式 + 可落盘规则」，产出三态决策：
``ALLOW`` / ``ASK`` / ``DENY``。规则优先级：deny > allow > ask > 模式默认。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from nono_agent.permissions.rules import PermissionRule, RuleAction


class PermissionMode(StrEnum):
    """会话权限模式 —— 决定未命中显式规则时的默认行为。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS = "bypass"


class PermissionDecision(StrEnum):
    """权限决策 —— 三态，供工具执行器消费。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionPolicy:
    """会话权限策略 —— 模式 + 有序规则。

    规则匹配顺序：先 deny（命中即拒绝），再 allow，再 ask；
    全部未命中时回落到模式默认值。
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        rules: list[PermissionRule] | None = None,
    ) -> None:
        self.mode = mode
        self._rules: list[PermissionRule] = list(rules) if rules else []

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------

    def add_rule(self, rule: PermissionRule) -> None:
        """追加一条规则。"""
        self._rules.append(rule)

    def add_rules(self, rules: list[PermissionRule]) -> None:
        """批量追加规则。"""
        self._rules.extend(rules)

    @property
    def rules(self) -> list[PermissionRule]:
        return list(self._rules)

    def set_mode(self, mode: PermissionMode) -> None:
        """切换会话模式。"""
        self.mode = mode

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

        Args:
            tool_name: 工具名（如 ``write_file``、``bash``）
            input_params: 工具输入参数（用于 path/bash 规则匹配）
            has_write_side_effects: 工具是否声明写副作用（决定模式默认行为）
        """
        params = input_params or {}

        # 1. deny 规则优先 —— 即使 bypass 模式也尊重显式 deny
        for rule in self._rules:
            if rule.action is RuleAction.DENY and rule.matches(tool_name, params):
                return PermissionDecision.DENY

        # 2. allow 规则
        for rule in self._rules:
            if rule.action is RuleAction.ALLOW and rule.matches(tool_name, params):
                return PermissionDecision.ALLOW

        # 3. ask 规则
        for rule in self._rules:
            if rule.action is RuleAction.ASK and rule.matches(tool_name, params):
                return PermissionDecision.ASK

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
        }
    """
    mode_raw = data.get("mode", "default")
    try:
        mode = PermissionMode(mode_raw)
    except ValueError:
        mode = PermissionMode.DEFAULT

    rules: list[PermissionRule] = []
    for item in data.get("rules", []):
        if not isinstance(item, dict):
            continue
        try:
            rules.append(PermissionRule.from_dict(item))
        except (KeyError, ValueError):
            continue

    return PermissionPolicy(mode=mode, rules=rules)


def policy_from_settings(settings: Any) -> PermissionPolicy:
    """从合并后的 Settings 读取 ``permissions`` 段并构建 PermissionPolicy。

    未配置 ``permissions`` 时返回默认策略。
    """
    data = settings.get("permissions", {}) if settings is not None else {}
    if not isinstance(data, dict):
        data = {}
    return policy_from_dict(data)
