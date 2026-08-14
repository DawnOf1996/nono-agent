"""Tests for the plugins package."""

import json
from pathlib import Path

from nono_agent.plugins import (
    FileSystemPluginLoader,
    activate_agents,
    activate_commands,
    activate_mcp,
    activate_rules,
    activate_skills,
    load_manifest,
)


def _make_plugin(root: Path) -> Path:
    plugin_dir = root / ".nono" / "plugins" / "my-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "my-plugin",
                "version": "1.0.0",
                "description": "test plugin",
                "rules_dir": "rules",
                "skills_dir": "skills",
                "agents_dir": "agents",
                "mcp": {"filesystem": {"command": "npx", "args": ["-y", "fs"]}},
                "permissions": {"rules": [{"action": "allow", "tool_name": "read_file"}]},
                "commands": {
                    "mycmd": {
                        "description": "My command",
                        "response": "hello from plugin",
                    }
                },
            }
        )
    )
    (plugin_dir / "rules").mkdir()
    (plugin_dir / "rules" / "style.md").write_text("---\nglobs: ['*.py']\n---\nUse 4 spaces.\n")
    (plugin_dir / "skills" / "review").mkdir(parents=True)
    (plugin_dir / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nReview instructions.\n"
    )
    (plugin_dir / "agents").mkdir()
    (plugin_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Code reviewer\ntools: ['read_file']\n---\nYou review.\n"
    )
    return plugin_dir


class TestManifest:
    def test_load_manifest(self, tmp_path):
        plugin_dir = _make_plugin(tmp_path)
        manifest = load_manifest(plugin_dir)
        assert manifest is not None
        assert manifest.name == "my-plugin"
        assert manifest.version == "1.0.0"

    def test_missing_manifest(self, tmp_path):
        assert load_manifest(tmp_path / "empty") is None

    def test_invalid_manifest(self, tmp_path):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({"version": "1.0"}))  # missing name
        assert load_manifest(d) is None


class TestLoader:
    def test_discover(self, tmp_path):
        _make_plugin(tmp_path)
        loader = FileSystemPluginLoader(user_plugins_dir=tmp_path / "no-user")
        manifests = loader.load(tmp_path)
        assert len(manifests) == 1
        assert manifests[0].name == "my-plugin"

    def test_empty(self, tmp_path):
        loader = FileSystemPluginLoader(user_plugins_dir=tmp_path / "no-user")
        assert loader.load(tmp_path) == []


class TestActivate:
    def test_activate_all(self, tmp_path):
        plugin_dir = _make_plugin(tmp_path)
        manifest = load_manifest(plugin_dir)

        rules = activate_rules(manifest)
        assert len(rules) == 1
        assert rules[0].scope == "plugin"
        assert rules[0].globs == ["*.py"]

        skills = activate_skills(manifest)
        assert len(skills) == 1
        assert skills[0].name == "review"

        agents = activate_agents(manifest)
        assert len(agents) == 1
        assert agents[0].name == "reviewer"
        assert agents[0].tools == ["read_file"]

        mcp = activate_mcp(manifest)
        assert "filesystem" in mcp
        assert mcp["filesystem"].command == "npx"

        from nono_agent.plugins import activate_permissions

        perms = activate_permissions(manifest)
        assert perms["rules"][0]["tool_name"] == "read_file"

        commands = activate_commands(manifest)
        assert len(commands) == 1
        assert commands[0].name == "mycmd"
        assert commands[0].execute([], None) == "hello from plugin"
