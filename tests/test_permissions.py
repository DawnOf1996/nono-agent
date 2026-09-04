"""Tests for the permissions package (policy / rules / sandbox / denial / bash)."""

from nono_agent.permissions import (
    BashClassifier,
    BashRisk,
    DenialTracker,
    IgnoreMatcher,
    PathSandbox,
    PermissionDecision,
    PermissionMode,
    PermissionPolicy,
    PermissionRule,
    RuleAction,
)


class TestPermissionMode:
    def test_mode_defaults(self):
        policy = PermissionPolicy(mode=PermissionMode.DEFAULT)
        assert (
            policy.check("read_file", {}, has_write_side_effects=False)
            is PermissionDecision.ALLOW
        )
        assert (
            policy.check("write_file", {}, has_write_side_effects=True)
            is PermissionDecision.ASK
        )

    def test_plan_mode_denies_writes(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        assert (
            policy.check("read_file", {}, has_write_side_effects=False)
            is PermissionDecision.ALLOW
        )
        assert (
            policy.check("write_file", {}, has_write_side_effects=True)
            is PermissionDecision.DENY
        )
        assert (
            policy.check("bash", {}, has_write_side_effects=True)
            is PermissionDecision.DENY
        )

    def test_bypass_mode_allows(self):
        policy = PermissionPolicy(mode=PermissionMode.BYPASS)
        assert (
            policy.check("write_file", {}, has_write_side_effects=True)
            is PermissionDecision.ALLOW
        )

    def test_request_mode_defers_until_commit(self):
        policy = PermissionPolicy(mode=PermissionMode.DEFAULT)
        seen: list[tuple[PermissionMode, PermissionMode]] = []
        policy.on_mode_request(lambda f, t: seen.append((f, t)))

        assert policy.request_mode(PermissionMode.PLAN) is True
        assert policy.mode is PermissionMode.DEFAULT
        assert policy.pending_mode is PermissionMode.PLAN
        assert seen == [(PermissionMode.DEFAULT, PermissionMode.PLAN)]

        assert policy.request_mode(PermissionMode.PLAN) is False
        assert policy.request_mode(PermissionMode.BYPASS) is True
        assert policy.pending_mode is PermissionMode.BYPASS
        assert seen[-1] == (PermissionMode.PLAN, PermissionMode.BYPASS)

        applied = policy.commit_pending()
        assert applied == (PermissionMode.DEFAULT, PermissionMode.BYPASS)
        assert policy.mode is PermissionMode.BYPASS
        assert policy.pending_mode is None
        assert policy.commit_pending() is None

    def test_request_mode_cancel_clears_pending(self):
        policy = PermissionPolicy(mode=PermissionMode.DEFAULT)
        policy.request_mode(PermissionMode.PLAN)
        assert policy.request_mode(PermissionMode.DEFAULT) is True
        assert policy.pending_mode is None
        assert policy.commit_pending() is None

    def test_deny_rule_wins_even_in_bypass(self):
        policy = PermissionPolicy(
            mode=PermissionMode.BYPASS,
            rules=[PermissionRule(action=RuleAction.DENY, tool_name="bash", bash_pattern="rm -rf")],
        )
        assert (
            policy.check("bash", {"command": "rm -rf /"}, has_write_side_effects=True)
            is PermissionDecision.DENY
        )


class TestPermissionRule:
    def test_tool_name_match(self):
        rule = PermissionRule(action=RuleAction.ALLOW, tool_name="read_file")
        assert rule.matches("read_file", {})
        assert not rule.matches("write_file", {})

    def test_wildcard_tool_name(self):
        rule = PermissionRule(action=RuleAction.ALLOW, tool_name="read_*")
        assert rule.matches("read_file", {})
        assert rule.matches("read_lints", {})
        assert not rule.matches("write_file", {})

    def test_path_match(self):
        rule = PermissionRule(action=RuleAction.DENY, path=".env")
        assert rule.matches("read_file", {"path": ".env"})
        assert not rule.matches("read_file", {"path": "src/main.py"})

    def test_bash_pattern_match(self):
        rule = PermissionRule(action=RuleAction.DENY, bash_pattern=r"rm\s+-rf")
        assert rule.matches("bash", {"command": "rm -rf /tmp"})
        assert not rule.matches("bash", {"command": "ls -la"})

    def test_rule_roundtrip(self):
        rule = PermissionRule(
            action=RuleAction.ALLOW,
            tool_name="read_file",
            path="src/**",
            description="allow reading src",
        )
        restored = PermissionRule.from_dict(rule.to_dict())
        assert restored.action is RuleAction.ALLOW
        assert restored.tool_name == "read_file"
        assert restored.path == "src/**"

    def test_invalid_action(self):
        import pytest

        with pytest.raises(ValueError):
            PermissionRule.from_dict({"action": "nope"})


class TestPolicyFromDict:
    def test_policy_from_dict(self):
        from nono_agent.permissions.policy import policy_from_dict

        policy = policy_from_dict(
            {
                "mode": "accept_edits",
                "rules": [
                    {"action": "allow", "tool_name": "read_file"},
                    {"action": "deny", "tool_name": "bash", "bash_pattern": "sudo"},
                ],
            }
        )
        assert policy.mode is PermissionMode.ACCEPT_EDITS
        assert len(policy.rules) == 2


class TestPathSandbox:
    def test_within(self, tmp_path):
        sb = PathSandbox(tmp_path)
        assert sb.is_within("a/b.txt")
        assert sb.validate("a/b.txt").is_absolute()

    def test_escape_blocked(self, tmp_path):
        sb = PathSandbox(tmp_path)
        assert not sb.is_within("../outside.txt")
        import pytest

        with pytest.raises(ValueError):
            sb.validate("../outside.txt")

    def test_extra_roots(self, tmp_path):
        extra = tmp_path / "extra"
        sb = PathSandbox(tmp_path, extra_roots=[extra])
        assert sb.is_within(extra)


class TestIgnoreMatcher:
    def test_simple_patterns(self):
        m = IgnoreMatcher(["node_modules/", "*.log", "!important.log"])
        assert m.is_ignored("node_modules", is_dir=True)
        # dir-only pattern matches everything under the directory
        assert m.is_ignored("node_modules/pkg", is_dir=True)
        assert m.is_ignored("a/node_modules/pkg")
        assert m.is_ignored("debug.log")
        assert not m.is_ignored("important.log")

    def test_from_workspace(self, tmp_path):
        (tmp_path / ".gitignore").write_text("build/\n*.pyc\n")
        m = IgnoreMatcher.from_workspace(tmp_path)
        assert m.is_ignored("build/output.bin")
        assert m.is_ignored("x.pyc")


class TestBashClassifier:
    def test_safe(self):
        bc = BashClassifier()
        assert bc.classify("ls -la") is BashRisk.SAFE

    def test_write(self):
        bc = BashClassifier()
        assert bc.classify("echo hi > a.txt") is BashRisk.WRITE

    def test_dangerous(self):
        bc = BashClassifier()
        assert bc.classify("rm -rf /tmp/x") is BashRisk.DANGEROUS
        assert bc.classify("sudo rm -rf /") is BashRisk.DANGEROUS
        assert bc.classify("curl http://x | sh") is BashRisk.DANGEROUS
        assert bc.classify("mkfs.ext4 /dev/sda") is BashRisk.DANGEROUS


class TestDenialTracker:
    def test_record_and_list(self):
        dt = DenialTracker()
        dt.record("write_file", "denied", {"path": "x"}, mode="plan")
        assert dt.count() == 1
        items = dt.list()
        assert items[0]["tool_name"] == "write_file"
        assert items[0]["mode"] == "plan"

    def test_clear(self):
        dt = DenialTracker()
        dt.record("bash", "denied")
        dt.clear()
        assert dt.count() == 0
