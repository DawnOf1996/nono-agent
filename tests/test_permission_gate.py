"""权限拦截链测试 —— 重点是**顺序**。

优先级矩阵是这里的核心：每个「收紧项」都必须赢过每个「放宽项」。
只测单个检查点的话，"某条捷径绕过了另一条规则"这种漏洞根本测不出来 ——
因为单点测试里每条规则都"工作正常"。
"""

from __future__ import annotations

import pytest

from nono_agent.hooks.user_hooks import BeforeToolVerdict
from nono_agent.permissions import (
    GateStep,
    PermissionDecision,
    PermissionGate,
    PermissionMode,
    PermissionPolicy,
    PermissionRule,
    RuleAction,
    SafetyBaseline,
)
from nono_agent.permissions.baseline import BaselineSeverity
from nono_agent.tools.protocol import ToolBase

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


class _Ctx:
    """最小 ToolUseContext —— 链只需要 cwd / session_id / policy。"""

    def __init__(self, cwd: str = "/tmp/work", policy: PermissionPolicy | None = None):
        self.cwd = cwd
        self.session_id = "s1"
        self.policy = policy


class _Tool(ToolBase):
    """默认写副作用工具。"""

    _name = "fake_tool"
    _description = "fake"
    _input_schema = {"type": "object", "properties": {}}
    _has_write_side_effects = True

    async def execute(self, input_params, context):  # pragma: no cover - 不该被调到
        raise AssertionError("execute() should not run in permission tests")


class _ReadTool(_Tool):
    _name = "fake_read"
    _has_write_side_effects = False


class _AlwaysAskTool(_Tool):
    _name = "always_ask_tool"
    _always_requires_approval = True


class _RefusingTool(_Tool):
    _name = "refusing_tool"

    def evaluate_permission(self, input_params, context):
        return PermissionDecision.DENY


class _PathTool(_Tool):
    """带 path 参数的写工具 —— 给安全底线用。"""

    _name = "fake_path_tool"
    _input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }


class _Hook:
    """假 hook 执行器 —— 固定返回一个判定。"""

    def __init__(self, decision: PermissionDecision) -> None:
        self._decision = decision
        self.calls: list[tuple[str, dict]] = []

    async def check_before_tool(self, tool_name, tool_input, context):
        self.calls.append((tool_name, tool_input))
        return BeforeToolVerdict(decision=self._decision, reason="hook says so")


def _gate(
    policy: PermissionPolicy | None = None,
    *,
    baseline: SafetyBaseline | None = None,
    hooks=None,
) -> PermissionGate:
    return PermissionGate(
        policy=policy or PermissionPolicy(),
        baseline=baseline or SafetyBaseline(),
        hooks=hooks,
    )


async def _run(gate: PermissionGate, tool, params=None, ctx=None):
    ctx = ctx or _Ctx()
    return await gate.evaluate(tool, tool.name, params or {}, ctx)


# ---------------------------------------------------------------------------
# 链的顺序
# ---------------------------------------------------------------------------


class TestChainOrder:
    @pytest.mark.asyncio
    async def test_steps_are_in_declared_order(self):
        gate = _gate()
        assert gate.steps == (
            "hook_pre",
            "rule_deny",
            "tool_verdict",
            "tool_always_ask",
            "safety_baseline",
            "plan_mode",
            "rule_ask",
            "auto_approve",
            "rule_ask_content",
            "mode_bypass",
            "rule_allow",
            "default",
        )

    @pytest.mark.asyncio
    async def test_default_asks_for_write_tool(self):
        outcome = await _run(_gate(), _Tool())
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.DEFAULT

    @pytest.mark.asyncio
    async def test_default_allows_read_tool(self):
        outcome = await _run(_gate(), _ReadTool())
        assert outcome.decision is PermissionDecision.ALLOW
        assert outcome.step is GateStep.DEFAULT

    @pytest.mark.asyncio
    async def test_bypass_allows_when_nothing_tightens(self):
        policy = PermissionPolicy(mode=PermissionMode.BYPASS)
        outcome = await _run(_gate(policy), _Tool())
        assert outcome.decision is PermissionDecision.ALLOW
        assert outcome.step is GateStep.MODE_BYPASS


class TestPriorityMatrix:
    """收紧项 × 放宽项 —— 每一格都必须由收紧项赢。

    这是整套设计里唯一能证明"顺序没被绕过"的东西。
    """

    # ---- 收紧项：构造出"能收紧"的策略 ----

    @staticmethod
    def _deny_rule() -> PermissionPolicy:
        return PermissionPolicy(
            rules=[PermissionRule(action=RuleAction.DENY, tool_name="fake_tool", id="r-deny")]
        )

    @staticmethod
    def _baseline_hit(cwd: str) -> SafetyBaseline:
        """指向 .git/ 的路径必然命中底线。"""
        return SafetyBaseline()

    @staticmethod
    def _plan_mode() -> PermissionPolicy:
        return PermissionPolicy(mode=PermissionMode.PLAN)

    # ---- 放宽项 ----

    @staticmethod
    def _bypass() -> PermissionPolicy:
        return PermissionPolicy(mode=PermissionMode.BYPASS)

    @staticmethod
    def _allow_rule() -> PermissionPolicy:
        return PermissionPolicy(
            rules=[PermissionRule(action=RuleAction.ALLOW, tool_name="fake_tool", id="r-allow")]
        )

    @staticmethod
    def _auto_approve() -> PermissionPolicy:
        return PermissionPolicy(
            auto_approve=[
                PermissionRule(action=RuleAction.ALLOW, tool_name="fake_tool", id="r-auto")
            ]
        )

    def _merge(self, tight: PermissionPolicy, loose: PermissionPolicy) -> PermissionPolicy:
        """把收紧项与放宽项合到一份策略里。"""
        return PermissionPolicy(
            mode=loose.mode if loose.mode is not PermissionMode.DEFAULT else tight.mode,
            rules=[*tight.rules, *loose.rules],
            auto_approve=loose.auto_approve_rules,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tight_kind,loose_kind,expected",
        [
            ("deny", "bypass", PermissionDecision.DENY),
            ("deny", "allow", PermissionDecision.DENY),
            ("deny", "auto", PermissionDecision.DENY),
            ("always_ask", "bypass", PermissionDecision.ASK),
            ("always_ask", "allow", PermissionDecision.ASK),
            ("always_ask", "auto", PermissionDecision.ASK),
            # plan 与 bypass 是**互斥的模式** —— 一份策略只有一个 mode，
            # 「plan 撞上 bypass」这种组合不存在。能与 plan 共存的放宽项是
            # 规则类的（allow / auto_approve），下面两格才是有意义的。
            ("plan", "allow", PermissionDecision.DENY),
            ("plan", "auto", PermissionDecision.DENY),
        ],
    )
    async def test_tight_always_beats_loose(self, tight_kind, loose_kind, expected):
        loose = {
            "bypass": self._bypass(),
            "allow": self._allow_rule(),
            "auto": self._auto_approve(),
        }[loose_kind]
        tool = _Tool()

        if tight_kind == "deny":
            policy = self._merge(self._deny_rule(), loose)
            outcome = await _run(_gate(policy), tool)
        elif tight_kind == "always_ask":
            policy = self._merge(PermissionPolicy(), loose)
            outcome = await _run(_gate(policy), _AlwaysAskTool())
        elif tight_kind == "plan":
            policy = self._merge(self._plan_mode(), loose)
            outcome = await _run(_gate(policy), tool)
        else:  # pragma: no cover
            raise AssertionError(tight_kind)

        assert outcome.decision is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "loose_kind",
        ["bypass", "allow", "auto"],
    )
    async def test_baseline_beats_every_loose(self, loose_kind, tmp_path):
        """安全底线谁也绕不过 —— 连 bypass 与「不再询问」都不行。"""
        loose = {
            "bypass": self._bypass(),
            "allow": PermissionPolicy(
                rules=[PermissionRule(action=RuleAction.ALLOW, tool_name="fake_path_tool")]
            ),
            "auto": PermissionPolicy(
                auto_approve=[
                    PermissionRule(action=RuleAction.ALLOW, tool_name="fake_path_tool")
                ]
            ),
        }[loose_kind]

        # 指向受保护目录的路径
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        policy = PermissionPolicy(
            mode=loose.mode,
            rules=loose.rules,
            auto_approve=loose.auto_approve_rules,
        )
        outcome = await _run(
            _gate(policy),
            _PathTool(),
            {"path": str(git_dir / "config")},
            _Ctx(cwd=str(tmp_path)),
        )
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.SAFETY_BASELINE

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "loose_kind",
        ["bypass", "allow", "auto"],
    )
    async def test_tool_refusal_beats_every_loose(self, loose_kind):
        """工具自己的绝对否决，谁都绕不过。"""
        loose = {
            "bypass": self._bypass(),
            "allow": PermissionPolicy(
                rules=[PermissionRule(action=RuleAction.ALLOW, tool_name="refusing_tool")]
            ),
            "auto": PermissionPolicy(
                auto_approve=[
                    PermissionRule(action=RuleAction.ALLOW, tool_name="refusing_tool")
                ]
            ),
        }[loose_kind]
        outcome = await _run(_gate(loose), _RefusingTool())
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.step is GateStep.TOOL_VERDICT


# ---------------------------------------------------------------------------
# 规则语义
# ---------------------------------------------------------------------------


class TestRuleSemantics:
    @pytest.mark.asyncio
    async def test_deny_beats_ask_beats_allow(self):
        policy = PermissionPolicy(
            rules=[
                PermissionRule(action=RuleAction.ALLOW, tool_name="t"),
                PermissionRule(action=RuleAction.ASK, tool_name="t"),
                PermissionRule(action=RuleAction.DENY, tool_name="t"),
            ]
        )
        assert policy.check("t", {}, has_write_side_effects=True) is PermissionDecision.DENY

    @pytest.mark.asyncio
    async def test_ask_beats_allow(self):
        policy = PermissionPolicy(
            rules=[
                PermissionRule(action=RuleAction.ALLOW, tool_name="t"),
                PermissionRule(action=RuleAction.ASK, tool_name="t"),
            ]
        )
        assert policy.check("t", {}, has_write_side_effects=True) is PermissionDecision.ASK

    @pytest.mark.asyncio
    async def test_tool_level_ask_not_overridden_by_auto_approve(self):
        """「这个工具每次都问」是硬约束 —— 白名单覆盖不了。"""
        policy = PermissionPolicy(
            rules=[PermissionRule(action=RuleAction.ASK, tool_name="fake_tool")],
            auto_approve=[
                PermissionRule(action=RuleAction.ALLOW, tool_name="fake_tool")
            ],
        )
        outcome = await _run(_gate(policy), _Tool())
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.RULE_ASK

    @pytest.mark.asyncio
    async def test_content_level_ask_overridden_by_auto_approve(self):
        """「这类操作要问」可以被「这些命令不再问」覆盖。"""
        policy = PermissionPolicy(
            rules=[
                PermissionRule(
                    action=RuleAction.ASK,
                    tool_name="fake_tool",
                    bash_pattern="npm publish",
                )
            ],
            auto_approve=[
                PermissionRule(
                    action=RuleAction.ALLOW,
                    tool_name="fake_tool",
                    bash_pattern="npm publish",
                )
            ],
        )
        outcome = await _run(
            _gate(policy), _Tool(), {"command": "npm publish --tag next"}
        )
        assert outcome.decision is PermissionDecision.ALLOW
        assert outcome.step is GateStep.AUTO_APPROVE

    @pytest.mark.asyncio
    async def test_content_ask_wins_without_auto_approve(self):
        policy = PermissionPolicy(
            rules=[
                PermissionRule(
                    action=RuleAction.ASK,
                    tool_name="fake_tool",
                    bash_pattern="npm publish",
                )
            ]
        )
        outcome = await _run(_gate(policy), _Tool(), {"command": "npm publish"})
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.RULE_ASK_CONTENT

    @pytest.mark.asyncio
    async def test_ask_rule_beats_bypass(self):
        """「问」的优先级高于「别再问我」模式。"""
        policy = PermissionPolicy(
            mode=PermissionMode.BYPASS,
            rules=[PermissionRule(action=RuleAction.ASK, tool_name="fake_tool")],
        )
        outcome = await _run(_gate(policy), _Tool())
        assert outcome.decision is PermissionDecision.ASK


# ---------------------------------------------------------------------------
# 工具侧协议
# ---------------------------------------------------------------------------


class TestToolProtocol:
    def test_default_evaluate_permission(self):
        assert _Tool().evaluate_permission({}, None) is PermissionDecision.ASK
        assert _ReadTool().evaluate_permission({}, None) is PermissionDecision.ALLOW

    def test_should_prompt_delegates(self):
        assert _Tool().should_prompt_for_permission({}, None) is True
        assert _ReadTool().should_prompt_for_permission({}, None) is False

    def test_always_requires_approval_default(self):
        assert _Tool().always_requires_approval is False
        assert _AlwaysAskTool().always_requires_approval is True

    @pytest.mark.asyncio
    async def test_bash_risk_mapping(self):
        """BashClassifier 恢复成真正的闸门。"""
        from nono_agent.tools.builtin.bash import BashTool

        tool = BashTool()
        assert (
            tool.evaluate_permission({"command": "ls -la"}, None)
            is PermissionDecision.ALLOW
        )
        assert (
            tool.evaluate_permission({"command": "echo hi > a.txt"}, None)
            is PermissionDecision.ASK
        )
        # DANGEROUS → ASK（用户保留话语权），不直接拒
        assert (
            tool.evaluate_permission({"command": "rm -rf /tmp/x"}, None)
            is PermissionDecision.ASK
        )

    @pytest.mark.asyncio
    async def test_missing_evaluate_permission_falls_back_to_static(self):
        """没实现新接口的工具 → 退回静态声明；再没有就当有写副作用。"""

        class _Legacy:
            name = "legacy"
            has_write_side_effects = False

        class _Bare:
            name = "bare"

        from nono_agent.permissions.gate import tool_verdict_of

        assert tool_verdict_of(_Legacy(), {}, None) is PermissionDecision.ALLOW
        assert tool_verdict_of(_Bare(), {}, None) is PermissionDecision.ASK

    @pytest.mark.asyncio
    async def test_broken_evaluate_permission_fails_closed(self):
        class _Broken:
            name = "broken"

            def evaluate_permission(self, input_params, context):
                raise RuntimeError("boom")

        from nono_agent.permissions.gate import tool_verdict_of

        assert tool_verdict_of(_Broken(), {}, None) is PermissionDecision.ASK


# ---------------------------------------------------------------------------
# 安全底线
# ---------------------------------------------------------------------------


class TestSafetyBaseline:
    def test_data_dir_credentials_denied(self, tmp_path, monkeypatch):
        """数据目录的 credentials 读都不行。"""
        from nono_agent import paths

        data_dir = tmp_path / ".nono-agent"
        data_dir.mkdir()
        (data_dir / "credentials").write_text("SECRET", encoding="utf-8")
        monkeypatch.setenv(paths.ENV_DATA_DIR, str(data_dir))
        paths.reset_user_home_cache()
        try:
            hit = SafetyBaseline().check(
                "read_file", {"path": str(data_dir / "credentials")}, str(tmp_path)
            )
            assert hit is not None
            assert hit.severity is BaselineSeverity.DENY
        finally:
            paths.reset_user_home_cache()

    def test_command_touching_credentials_denied(self, tmp_path, monkeypatch):
        from nono_agent import paths

        data_dir = tmp_path / ".nono-agent"
        data_dir.mkdir()
        monkeypatch.setenv(paths.ENV_DATA_DIR, str(data_dir))
        paths.reset_user_home_cache()
        try:
            hit = SafetyBaseline().check(
                "bash",
                {"command": "cat ~/.nono-agent/credentials"},
                str(tmp_path),
            )
            assert hit is not None
            assert hit.severity is BaselineSeverity.DENY
        finally:
            paths.reset_user_home_cache()

    def test_git_dir_asks(self, tmp_path):
        git = tmp_path / ".git"
        git.mkdir()
        hit = SafetyBaseline().check(
            "read_file", {"path": str(git / "config")}, str(tmp_path)
        )
        assert hit is not None
        assert hit.severity is BaselineSeverity.ASK

    def test_env_file_asks(self, tmp_path):
        hit = SafetyBaseline().check(
            "read_file", {"path": str(tmp_path / ".env")}, str(tmp_path)
        )
        assert hit is not None
        assert hit.severity is BaselineSeverity.ASK

    def test_pem_file_asks(self, tmp_path):
        hit = SafetyBaseline().check(
            "read_file", {"path": str(tmp_path / "key.pem")}, str(tmp_path)
        )
        assert hit is not None
        assert hit.severity is BaselineSeverity.ASK

    def test_normal_file_not_hit(self, tmp_path):
        assert (
            SafetyBaseline().check(
                "read_file", {"path": str(tmp_path / "src" / "main.py")}, str(tmp_path)
            )
            is None
        )

    def test_relative_path_resolved_against_cwd(self, tmp_path):
        (tmp_path / ".git").mkdir()
        hit = SafetyBaseline().check("read_file", {"path": ".git/config"}, str(tmp_path))
        assert hit is not None
        assert hit.severity is BaselineSeverity.ASK


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class TestHookIntegration:
    @pytest.mark.asyncio
    async def test_hook_deny_wins(self):
        hook = _Hook(PermissionDecision.DENY)
        outcome = await _run(_gate(hooks=hook), _Tool())
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.step is GateStep.HOOK_PRE

    @pytest.mark.asyncio
    async def test_hook_ask_wins(self):
        hook = _Hook(PermissionDecision.ASK)
        outcome = await _run(_gate(hooks=hook), _Tool())
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.HOOK_PRE

    @pytest.mark.asyncio
    async def test_hook_allow_does_not_skip_safety_baseline(self, tmp_path):
        """hook 说允许 → 继续走完整链，底线照样查。"""
        git = tmp_path / ".git"
        git.mkdir()
        hook = _Hook(PermissionDecision.ALLOW)
        outcome = await _run(
            _gate(hooks=hook),
            _PathTool(),
            {"path": str(git / "config")},
            _Ctx(cwd=str(tmp_path)),
        )
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.SAFETY_BASELINE

    @pytest.mark.asyncio
    async def test_hook_allow_does_not_skip_deny_rule(self):
        hook = _Hook(PermissionDecision.ALLOW)
        policy = PermissionPolicy(
            rules=[PermissionRule(action=RuleAction.DENY, tool_name="fake_tool")]
        )
        outcome = await _run(_gate(policy, hooks=hook), _Tool())
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.step is GateStep.RULE_DENY

    @pytest.mark.asyncio
    async def test_hook_allow_skips_the_default_ask(self):
        """hook 明确放行 → 这次不必再问（无人值守 agent 的唯一放行口子）。"""
        hook = _Hook(PermissionDecision.ALLOW)
        outcome = await _run(_gate(hooks=hook), _Tool())
        assert outcome.decision is PermissionDecision.ALLOW
        assert outcome.step is GateStep.DEFAULT
        assert outcome.rule == "hook:allow"

    @pytest.mark.asyncio
    async def test_hook_allow_does_not_skip_tool_level_ask_rule(self):
        """用户明写的「这个工具每次都问」仍然生效 —— hook 不能拆规则。"""
        hook = _Hook(PermissionDecision.ALLOW)
        policy = PermissionPolicy(
            rules=[PermissionRule(action=RuleAction.ASK, tool_name="fake_tool")]
        )
        outcome = await _run(_gate(policy, hooks=hook), _Tool())
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.RULE_ASK


# ---------------------------------------------------------------------------
# plan 模式
# ---------------------------------------------------------------------------


class TestPlanMode:
    @pytest.mark.asyncio
    async def test_plan_denies_write(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        outcome = await _run(_gate(policy), _Tool())
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.step is GateStep.PLAN_MODE

    @pytest.mark.asyncio
    async def test_plan_allows_read(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        outcome = await _run(_gate(policy), _ReadTool())
        assert outcome.decision is PermissionDecision.ALLOW

    @pytest.mark.asyncio
    async def test_plan_allows_safe_bash(self):
        """`ls` 在 plan 模式下照常能跑 —— 按入参动态判定，不是静态写副作用。"""
        from nono_agent.tools.builtin.bash import BashTool

        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        outcome = await _run(_gate(policy), BashTool(), {"command": "ls -la"})
        assert outcome.decision is PermissionDecision.ALLOW

    @pytest.mark.asyncio
    async def test_plan_denies_write_bash(self):
        from nono_agent.tools.builtin.bash import BashTool

        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        outcome = await _run(_gate(policy), BashTool(), {"command": "echo x > a.txt"})
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.step is GateStep.PLAN_MODE


# ---------------------------------------------------------------------------
# 模式切换
# ---------------------------------------------------------------------------


class TestPrePlanMode:
    def test_exit_plan_restores_previous_mode(self):
        policy = PermissionPolicy(mode=PermissionMode.BYPASS)
        policy.request_mode(PermissionMode.PLAN)
        policy.commit_pending()
        assert policy.mode is PermissionMode.PLAN
        assert policy.pre_plan_mode is PermissionMode.BYPASS

    def test_pre_plan_mode_defaults_to_default(self):
        assert PermissionPolicy().pre_plan_mode is PermissionMode.DEFAULT

    @pytest.mark.asyncio
    async def test_exit_plan_mode_tool_restores_previous_mode(self):
        """从 bypass 进 plan 再退出，应该还在 bypass —— 不该被降级。"""
        from nono_agent.tools.builtin.plan import (
            EnterPlanModeTool,
            ExitPlanModeTool,
        )

        policy = PermissionPolicy(mode=PermissionMode.BYPASS)
        ctx = _Ctx(policy=policy)
        await EnterPlanModeTool().execute({}, ctx)
        policy.commit_pending()
        assert policy.mode is PermissionMode.PLAN

        await ExitPlanModeTool().execute({}, ctx)
        policy.commit_pending()
        assert policy.mode is PermissionMode.BYPASS

    @pytest.mark.asyncio
    async def test_exit_plan_mode_requires_approval(self):
        """模型不能自己解开用户设下的只读限制 —— 退出计划模式要有人点头。"""
        from nono_agent.tools.builtin.plan import ExitPlanModeTool

        assert ExitPlanModeTool().always_requires_approval is True

        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        outcome = await _run(_gate(policy), ExitPlanModeTool())
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.TOOL_ALWAYS_ASK

    @pytest.mark.asyncio
    async def test_exit_plan_mode_approval_immune_to_bypass(self):
        """连 bypass 都跳不过 —— 工具声明的必须人工确认是硬约束。"""
        from nono_agent.tools.builtin.plan import ExitPlanModeTool

        policy = PermissionPolicy(mode=PermissionMode.BYPASS)
        outcome = await _run(_gate(policy), ExitPlanModeTool())
        assert outcome.decision is PermissionDecision.ASK
        assert outcome.step is GateStep.TOOL_ALWAYS_ASK


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


class TestAudit:
    @pytest.mark.asyncio
    async def test_outcome_carries_step_and_rule(self):
        policy = PermissionPolicy(
            rules=[
                PermissionRule(
                    action=RuleAction.DENY,
                    tool_name="fake_tool",
                    description="不许跑",
                    id="rule-42",
                )
            ]
        )
        outcome = await _run(_gate(policy), _Tool())
        assert outcome.rule == "rule-42"
        assert outcome.step is GateStep.RULE_DENY

    def test_denial_tracker_records_step_and_rule(self):
        from nono_agent.permissions import DenialTracker

        tracker = DenialTracker()
        tracker.record(
            "bash", "denied", {}, mode="default", step="rule_deny", rule="r-1"
        )
        item = tracker.list()[0]
        assert item["step"] == "rule_deny"
        assert item["rule"] == "r-1"


# ---------------------------------------------------------------------------
# 端到端：落盘设置 → 策略 → 拦截链 → 执行器
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """证明「配了规则就真的生效」。

    改造前这条链是断的：设置被读进来，但没人把它接到引擎上 ——
    用户写了 rules，看着像生效了，其实一条都没跑过。
    """

    @staticmethod
    def _write_settings(root, payload: dict) -> None:
        import json

        d = root / ".nono-agent"
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    @pytest.mark.asyncio
    async def test_deny_rule_from_settings_blocks_execution(self, tmp_path):
        from nono_agent.core.types import ToolUseBlock
        from nono_agent.permissions.policy import policy_from_settings
        from nono_agent.settings import load_settings_from_env
        from nono_agent.tools.context import ToolUseContext
        from nono_agent.tools.executor import BatchToolExecutor
        from nono_agent.tools.registry import ToolRegistry

        self._write_settings(
            tmp_path,
            {
                "permissions": {
                    "rules": [
                        {
                            "action": "deny",
                            "tool_name": "fake_tool",
                            "description": "这个工具不许用",
                            "id": "r-deny-1",
                        }
                    ]
                }
            },
        )

        settings = load_settings_from_env(tmp_path)
        policy = policy_from_settings(settings)
        gate = PermissionGate(policy=policy)

        registry = ToolRegistry()
        registry.register(_Tool())
        ctx = ToolUseContext(cwd=str(tmp_path), policy=policy, gate=gate)

        blocks = [ToolUseBlock(id="t1", name="fake_tool", input={})]
        results = await BatchToolExecutor(registry).execute(blocks, ctx)

        assert len(results) == 1
        assert results[0].permission_denied is True
        assert results[0].result.is_error is True
        assert "不许用" in results[0].result.content

    @pytest.mark.asyncio
    async def test_auto_approve_from_settings_allows_execution(self, tmp_path):
        from nono_agent.core.types import ToolUseBlock
        from nono_agent.permissions.policy import policy_from_settings
        from nono_agent.settings import load_settings_from_env
        from nono_agent.tools.context import ToolUseContext
        from nono_agent.tools.executor import BatchToolExecutor
        from nono_agent.tools.registry import ToolRegistry

        self._write_settings(
            tmp_path,
            {
                "permissions": {
                    "auto_approve": [
                        {"action": "allow", "tool_name": "fake_tool", "id": "r-auto-1"}
                    ]
                }
            },
        )

        settings = load_settings_from_env(tmp_path)
        policy = policy_from_settings(settings)
        gate = PermissionGate(policy=policy)

        registry = ToolRegistry()
        registry.register(_Tool())
        ctx = ToolUseContext(cwd=str(tmp_path), policy=policy, gate=gate)

        blocks = [ToolUseBlock(id="t1", name="fake_tool", input={})]
        results = await BatchToolExecutor(registry).execute(blocks, ctx)

        # auto_approve 放行了 —— 但工具本身没有 execute 实现，
        # 所以结果是「执行报错」而不是「权限拒绝」。这两件事要分清。
        assert results[0].permission_denied is False

    @pytest.mark.asyncio
    async def test_write_tool_asks_when_no_checker(self, tmp_path):
        """没有交互检查器时，写工具仍走 ASK → 无检查器即拒绝（fail-closed）。"""
        from nono_agent.core.types import ToolUseBlock
        from nono_agent.permissions.policy import policy_from_settings
        from nono_agent.settings import load_settings_from_env
        from nono_agent.tools.context import ToolUseContext
        from nono_agent.tools.executor import BatchToolExecutor
        from nono_agent.tools.registry import ToolRegistry

        settings = load_settings_from_env(tmp_path)
        policy = policy_from_settings(settings)
        gate = PermissionGate(policy=policy)

        registry = ToolRegistry()
        registry.register(_Tool())
        ctx = ToolUseContext(cwd=str(tmp_path), policy=policy, gate=gate)

        blocks = [ToolUseBlock(id="t1", name="fake_tool", input={})]
        results = await BatchToolExecutor(registry).execute(blocks, ctx)
        assert results[0].permission_denied is True

    @pytest.mark.asyncio
    async def test_env_flag_mode_is_applied(self, tmp_path, monkeypatch):
        """``NONO_PERMISSION_MODE`` 真的能切模式 —— 改造前它只写进设置没人读。"""
        from nono_agent.permissions.policy import policy_from_settings
        from nono_agent.settings import load_settings_from_env

        monkeypatch.setenv("NONO_PERMISSION_MODE", "plan")
        settings = load_settings_from_env(tmp_path)
        policy = policy_from_settings(settings)
        assert policy.mode is PermissionMode.PLAN
