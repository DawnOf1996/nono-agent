"""Tests for rules discovery and merge."""

from nono_agent.rules import (
    FileSystemRuleLoader,
    list_effective_rules,
    merge_rules,
)
from nono_agent.rules.models import Rule


class TestMergeRules:
    def test_merge_orders_by_priority(self):
        rules = [
            Rule(name="a", content="rule A", source="a", priority=0),
            Rule(name="b", content="rule B", source="b", priority=10),
        ]
        merged = merge_rules(rules)
        assert merged.index("rule B") < merged.index("rule A")

    def test_globs_filter(self):
        rules = [
            Rule(name="always", content="always", source="x"),
            Rule(name="conditional", content="cond", source="y", globs=["*.py"]),
        ]
        merged = merge_rules(rules, active_paths=["main.py"])
        assert "always" in merged
        assert "cond" in merged

        merged2 = merge_rules(rules, active_paths=["main.txt"])
        assert "always" in merged2
        assert "cond" not in merged2


class TestFileSystemRuleLoader:
    def test_load_project_rules(self, tmp_path):
        rules_dir = tmp_path / ".nono" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "style.md").write_text("---\nglobs: ['*.py']\n---\nUse 4 spaces.\n")

        loader = FileSystemRuleLoader(user_rules_dir=tmp_path / "no-user-rules")
        rules = loader.load(tmp_path)
        assert len(rules) == 1
        assert rules[0].name == "style.md"
        assert rules[0].content == "Use 4 spaces."
        assert rules[0].globs == ["*.py"]

    def test_load_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Be concise.\n")
        loader = FileSystemRuleLoader(user_rules_dir=tmp_path / "no-user-rules")
        rules = loader.load(tmp_path)
        assert any(r.content == "Be concise." for r in rules)

    def test_empty_project(self, tmp_path):
        loader = FileSystemRuleLoader(user_rules_dir=tmp_path / "no-user-rules")
        assert loader.load(tmp_path) == []


class TestListEffectiveRules:
    def test_list(self):
        rules = [
            Rule(name="a", content="A", source="x", globs=["*.py"]),
            Rule(name="b", content="B", source="y"),
        ]
        effective = list_effective_rules(rules, active_paths=["x.py"])
        assert len(effective) == 2
