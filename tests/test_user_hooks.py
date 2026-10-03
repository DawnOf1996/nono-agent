"""Tests for user-configurable hooks (hooks.json)."""

import json
import sys

import pytest

from nono_agent.hooks import UserHookExecutor, UserHooksConfig

# 把 stdin 原样转到 stdout 的命令。钩子上下文经 stdin 以 JSON 传入
# （UserHookExecutor.run），所以「读 stdin」是跨平台都能验证上下文的最小动作。
# Windows 上没有 cat，more 是等价物。
_ECHO_STDIN = "cat" if sys.platform != "win32" else "more"


class TestUserHooksConfig:
    def test_from_file(self, tmp_path):
        cfg_file = tmp_path / "hooks.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "hooks": [
                        {"event": "before_tool", "matcher": "write_file", "command": "deny"},
                        {"event": "after_tool", "matcher": ".*", "command": "log"},
                    ]
                }
            )
        )
        config = UserHooksConfig.from_file(cfg_file)
        assert len(config.hooks) == 2
        assert config.hooks[0].event == "before_tool"

    def test_missing_file(self, tmp_path):
        config = UserHooksConfig.from_file(tmp_path / "nope.json")
        assert config.hooks == []

    def test_matcher(self):
        from nono_agent.hooks import UserHook

        hook = UserHook(event="before_tool", command="x", matcher="write_.*")
        assert hook.matches("write_file")
        assert not hook.matches("read_file")


class TestUserHookExecutor:
    @pytest.mark.asyncio
    async def test_run_passes_context(self):
        config = _config([{"event": "after_tool", "command": _ECHO_STDIN}])
        executor = UserHookExecutor(config)
        result = await executor.run(config.hooks[0], {"tool_name": "read_file"})
        assert result.exit_code == 0
        assert "read_file" in result.stdout

    @pytest.mark.asyncio
    async def test_before_tool_veto(self):
        config = _config([{"event": "before_tool", "matcher": "write_file", "command": "exit 1"}])
        executor = UserHookExecutor(config)
        verdict = await executor.check_before_tool(
            "write_file", {"path": "x"}, {}
        )
        assert not verdict.allowed

    @pytest.mark.asyncio
    async def test_before_tool_allow(self):
        config = _config([{"event": "before_tool", "matcher": "write_file", "command": "exit 0"}])
        executor = UserHookExecutor(config)
        verdict = await executor.check_before_tool(
            "write_file", {"path": "x"}, {}
        )
        assert verdict.allowed

    @pytest.mark.asyncio
    async def test_matcher_filter(self):
        config = _config([{"event": "before_tool", "matcher": "bash", "command": "exit 1"}])
        executor = UserHookExecutor(config)
        # 不匹配的 tool 不触发否决
        verdict = await executor.check_before_tool("read_file", {}, {})
        assert verdict.allowed


def _config(hooks):
    from nono_agent.hooks import UserHook

    return UserHooksConfig(hooks=[UserHook.from_dict(h) for h in hooks])
