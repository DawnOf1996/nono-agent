"""Tests for the layered settings package."""

import json

from nono_agent.settings import load_settings


def monkey_user_home(monkeypatch, config_dir):
    """把 user_home 指到 config_dir。

    注意 ``user_home()`` 返回的是**配置目录本身**（``~/.nono-agent``），
    不是家目录 —— 拿 ``Path.home()`` 去套会差一层。

    sources.py 用的是 ``from ... import user_home`` 的独立绑定，只改
    paths 不够（conftest 里也是两个都打桩）。走 monkeypatch 而非直接赋值，
    避免跨用例泄漏。
    """
    from nono_agent import paths as paths_module
    from nono_agent.settings import sources as sources_mod

    def _home():
        return config_dir

    monkeypatch.setattr(paths_module, "user_home", _home)
    monkeypatch.setattr(sources_mod, "user_home", _home)


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
        (tmp_path / ".nono-agent").mkdir()
        (tmp_path / ".nono-agent" / "settings.json").write_text(
            json.dumps({"permissions": {"mode": "accept_edits"}})
        )
        s = load_settings(tmp_path, user_settings={})
        assert s.get("permissions.mode") == "accept_edits"

    def test_patch_project_settings_merges(self, tmp_path):
        from nono_agent.settings import patch_project_settings

        (tmp_path / ".nono-agent").mkdir()
        (tmp_path / ".nono-agent" / "settings.json").write_text(
            json.dumps(
                {
                    "permissions": {"mode": "plan"},
                    "mcp": {"servers": {"a": {"enabled": False}}},
                }
            )
        )
        patch_project_settings(tmp_path, {"mcp": {"servers": {"a": {"enabled": True}}}})
        s = load_settings(tmp_path, user_settings={})
        assert s.get("permissions.mode") == "plan"
        assert s.get("mcp.servers.a.enabled") is True


class TestUserSettingsAreNotProjectSettings:
    """用户级配置文件不能被向上查找当成项目级再读一遍。

    cwd 在 ``$HOME`` 下面时（``~/projects/foo``），向上找
    ``.nono-agent/settings.json`` 会撞上 ``~/.nono-agent/settings.json``。
    撞上了就凭空多出 project 层，``source_of()`` 会把 user 层的键误报成
    project，面板上的「被工作区覆盖」提示也会误报。

    三条用例都把整棵树塞进 ``tmp_path`` 里，且把「用户家目录」放在一个
    仍带项目配置的父目录之下 —— 否则 tmp_path 之上的真实机器目录会参与
    查找，让断言变得依赖跑测试的这台机器。
    """

    @staticmethod
    def _write(config_dir, payload):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "settings.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_user_file_not_reloaded_as_project(self, tmp_path, monkeypatch):
        from nono_agent.settings import load_project_settings, patch_user_settings

        home = tmp_path / "root" / "home"      # 类比 $HOME
        config = home / ".nono-agent"          # user_home() 返回的就是它
        config.mkdir(parents=True)
        monkey_user_home(monkeypatch, config)
        # 标记键：一旦用户级文件被当成项目级读回来，blob 里就会带上它
        patch_user_settings({"marker_from_user": {"enabled": True}})

        workspace = home / "projects" / "foo"
        workspace.mkdir(parents=True)

        assert "marker_from_user" not in json.dumps(load_project_settings(workspace))
        s = load_settings(workspace)
        assert s.get("marker_from_user.enabled") is True
        assert s.source_of("marker_from_user.enabled") == "user"

    def test_walk_does_not_escape_above_home(self, tmp_path, monkeypatch):
        """家目录之上不是项目根 —— 查找不该一路走到文件系统根。

        不设这条边界时，``$HOME`` 底下的工作区会把家目录自己的
        ``.nono-agent``（以及再往上的整层目录）当成项目配置。
        """
        from nono_agent.settings import load_project_settings

        root = tmp_path / "root"
        home = root / "home"
        config = home / ".nono-agent"
        config.mkdir(parents=True)
        monkey_user_home(monkeypatch, config)
        self._write(config, {"permissions": {"mode": "plan"}})
        # 家目录**之上**的配置不该被捡到
        self._write(root / ".nono-agent", {"permissions": {"mode": "bypass"}})
        inner = home / "projects" / "foo"
        inner.mkdir(parents=True)

        assert load_project_settings(inner) == {}

    def test_project_config_between_workspace_and_home_found(self, tmp_path, monkeypatch):
        from nono_agent.settings import load_project_settings

        home = tmp_path / "root" / "home"
        config = home / ".nono-agent"
        config.mkdir(parents=True)
        monkey_user_home(monkeypatch, config)
        self._write(config, {"permissions": {"mode": "plan"}})
        self._write(
            home / "myproject" / ".nono-agent", {"permissions": {"mode": "accept_edits"}}
        )

        assert load_project_settings(home / "myproject") == {
            "permissions": {"mode": "accept_edits"}
        }

    def test_project_config_above_workspace_below_home_found(self, tmp_path, monkeypatch):
        """工作区自己没配，往上到最近的祖先目录有 —— 应当被找到。"""
        from nono_agent.settings import load_project_settings

        home = tmp_path / "root" / "home"
        config = home / ".nono-agent"
        config.mkdir(parents=True)
        monkey_user_home(monkeypatch, config)
        self._write(home / "group" / ".nono-agent", {"permissions": {"mode": "bypass"}})
        inner = home / "group" / "svc" / "api"
        inner.mkdir(parents=True)

        assert load_project_settings(inner) == {"permissions": {"mode": "bypass"}}


class TestSettingsPolicyBridge:
    def test_policy_from_settings(self):
        from nono_agent.permissions import PermissionMode
        from nono_agent.permissions.policy import policy_from_settings

        s = load_settings(".", project_settings={"permissions": {"mode": "plan"}})
        policy = policy_from_settings(s)
        assert policy.mode is PermissionMode.PLAN
