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
        from nono_agent.adapters.testing import create_test_engine

        return create_test_engine()

    def test_model_and_cost(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")

        assert "claude-test" in registry.execute("model", [], ctx)
        assert "Cost:" in registry.execute("cost", [], ctx)
        assert "Context:" in registry.execute("context", [], ctx)

    def test_clear(self):
        engine = self._engine()
        registry = CommandRegistry()
        for cmd in create_builtin_commands(registry):
            registry.register(cmd)
        ctx = CommandContext(engine=engine, cwd=".")
        assert "cleared" in registry.execute("clear", [], ctx)
