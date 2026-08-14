"""Tests for the layered settings package."""

import json

from nono_agent.settings import load_settings


class TestLoadSettings:
    def test_default(self, tmp_path):
        s = load_settings(tmp_path, user_settings={}, project_settings={})
        assert s.get("permissions.mode") == "default"

    def test_project_overrides_default(self, tmp_path):
        s = load_settings(
            tmp_path,
            user_settings={},
            project_settings={"permissions": {"mode": "plan"}},
        )
        assert s.get("permissions.mode") == "plan"
        assert s.source_of("permissions.mode") == "project"

    def test_flag_highest_priority(self, tmp_path):
        s = load_settings(
            tmp_path,
            user_settings={},
            project_settings={"permissions": {"mode": "plan"}},
            flag_settings={"permissions": {"mode": "bypass"}},
        )
        assert s.get("permissions.mode") == "bypass"
        assert s.source_of("permissions.mode") == "flag"

    def test_deep_merge_preserves_other_keys(self, tmp_path):
        s = load_settings(
            tmp_path,
            project_settings={"permissions": {"mode": "plan"}},
            flag_settings={"permissions": {"rules": [{"action": "deny", "tool_name": "bash"}]}},
        )
        assert s.get("permissions.mode") == "plan"
        assert len(s.get("permissions.rules", [])) == 1

    def test_load_project_settings_from_disk(self, tmp_path):
        (tmp_path / ".nono").mkdir()
        (tmp_path / ".nono" / "settings.json").write_text(
            json.dumps({"permissions": {"mode": "accept_edits"}})
        )
        s = load_settings(tmp_path, user_settings={})
        assert s.get("permissions.mode") == "accept_edits"


class TestSettingsPolicyBridge:
    def test_policy_from_settings(self):
        from nono_agent.permissions import PermissionMode
        from nono_agent.permissions.policy import policy_from_settings

        s = load_settings(".", project_settings={"permissions": {"mode": "plan"}})
        policy = policy_from_settings(s)
        assert policy.mode is PermissionMode.PLAN
