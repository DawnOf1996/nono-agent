"""内置 Slash 命令（MVP 子集）：help/model/cost/context/clear。"""

from __future__ import annotations

from nono_agent.commands.registry import CommandContext, SlashCommand


class _SimpleCommand:
    """便利基类 —— 单函数命令。"""

    _name: str = ""
    _description: str = ""

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        raise NotImplementedError


class HelpCommand(_SimpleCommand):
    _name = "help"
    _description = "List available commands"

    def __init__(self, registry: object | None = None) -> None:
        self._registry = registry

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if self._registry is None:
            return "Commands: /help"
        names = self._registry.names()  # type: ignore[attr-defined]
        return "Available commands: " + ", ".join(f"/{n}" for n in names)


class ModelCommand(_SimpleCommand):
    _name = "model"
    _description = "Show the current model"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Model: (no engine attached)"
        return f"Model: {ctx.engine._config.effective_model}"


class CostCommand(_SimpleCommand):
    _name = "cost"
    _description = "Show session cost"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Cost: (no engine attached)"
        tracker = ctx.engine.cost_tracker
        usage = tracker.get_total_usage()
        return (
            f"Cost: ${tracker.get_total_cost():.4f} | "
            f"tokens in={usage.input_tokens} out={usage.output_tokens}"
        )


class ContextCommand(_SimpleCommand):
    _name = "context"
    _description = "Show context size"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Context: (no engine attached)"
        store = ctx.engine._store
        return f"Context: {len(store)} messages"


class ClearCommand(_SimpleCommand):
    _name = "clear"
    _description = "Clear the conversation"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Clear: (no engine attached)"
        ctx.engine.clear_session()
        return "Conversation cleared."


def create_builtin_commands(registry: object | None = None) -> list[SlashCommand]:
    """创建内置 Slash 命令列表。"""
    commands: list[SlashCommand] = [
        ModelCommand(),
        CostCommand(),
        ContextCommand(),
        ClearCommand(),
    ]
    commands.append(HelpCommand(registry))
    return commands
