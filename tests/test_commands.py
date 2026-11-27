"""Tests for the Slash command package."""

from nono_agent.commands import (
    CommandContext,
    CommandRegistry,
    is_slash_command,
    parse_slash,
)
from nono_agent.commands.builtin import create_builtin_commands


class TestParser:
    def test_parse_slash(self):
        assert parse_slash("/model") == ("model", [])
        assert parse_slash("/rules --verbose") == ("rules", ["--verbose"])

    def test_not_slash(self):
        assert parse_slash("hello world") is None
        assert parse_slash("") is None

    def test_is_slash(self):
        assert is_slash_command("/clear")
        assert not is_slash_command("not a command")


class TestRegistry:
    def _registry(self):
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        return registry

    def test_help_lists_commands(self):
        registry = self._registry()
        output = registry.execute("help", [], CommandContext())
        assert "/model" in output
        assert "/clear" in output

    def test_unknown_command(self):
        registry = self._registry()
        output = registry.execute("nope", [], CommandContext())
        assert "Unknown command" in output

    def test_model_requires_engine(self):
        registry = self._registry()
        output = registry.execute("model", [], CommandContext())
        assert "no engine" in output


class TestCommandsWithEngine:
    def _engine(self):
        from tests.helpers import create_test_engine

        return create_test_engine()

    def test_model_and_tokens(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")

        assert "claude-test" in registry.execute("model", [], ctx)
        assert "Tokens:" in registry.execute("tokens", [], ctx)
        assert "Context:" in registry.execute("context", [], ctx)
        switched = registry.execute("model", ["other"], ctx)
        assert "not supported" in switched
        assert "claude-test" in switched

    def test_clear(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")
        assert "cleared" in registry.execute("clear", [], ctx)

    def test_permission_show_and_set(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")

        assert "default" in registry.execute("permission", [], ctx)
        assert "plan" in registry.execute("permission", ["plan"], ctx)
        assert engine.permission_policy.mode.value == "default"
        assert engine.permission_policy.pending_mode.value == "plan"
        assert "Pending: plan" in registry.execute("permission", [], ctx)

    def test_permission_invalid(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")
        assert "Unknown mode" in registry.execute("permission", ["bogus"], ctx)

    def test_skills_and_rules_noop(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")
        # 默认无 skills/rules 也应返回非错误文本
        assert "No skills" in registry.execute("skills", [], ctx)

    def test_mcp_shows_startup_error(self):
        from types import SimpleNamespace

        from nono_agent.mcp.runtime import MCPServerStatus

        engine = SimpleNamespace(
            mcp_runtime=SimpleNamespace(
                enabled=True,
                statuses=[
                    MCPServerStatus(
                        name="username",
                        transport="http",
                        enabled=True,
                        running=False,
                        url="http://127.0.0.1:8765/mcp",
                        error="Client error '404 Not Found' for url 'http://127.0.0.1:8765/mcp'",
                    )
                ],
            )
        )
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")
        output = registry.execute("mcp", [], ctx)
        assert "[error]" in output
        assert "404 Not Found" in output
        assert "error:" in output

    def test_compact(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")
        # 少量消息不足以压缩 → removed 0
        assert "removed 0" in registry.execute("compact", [], ctx)
