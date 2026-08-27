"""内置 Slash 命令（MVP 子集）：help/model/tokens/context/clear 等。"""

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


class TokensCommand(_SimpleCommand):
    _name = "tokens"
    _description = "Show session token usage"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Tokens: (no engine attached)"
        tracker = ctx.engine.token_tracker
        usage = tracker.get_total_usage()
        return (
            f"Tokens: in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={usage.cache_read_input_tokens} "
            f"cache_creation={usage.cache_creation_input_tokens} "
            f"(total {usage.total_tokens})"
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


class RulesCommand(_SimpleCommand):
    _name = "rules"
    _description = "List effective rules"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Rules: (no engine attached)"
        from nono_agent.rules.merge import list_effective_rules

        rules = list_effective_rules(ctx.engine.rule_loader.load(ctx.cwd))
        if not rules:
            return "No rules loaded."
        lines = [f"- {r.name} ({r.scope}, {r.source})" for r in rules]
        return "Effective rules:\n" + "\n".join(lines)


class SkillsCommand(_SimpleCommand):
    _name = "skills"
    _description = "List skills, or enable/disable one (skills enable|disable <name>)"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Skills: (no engine attached)"

        if args and args[0] in ("enable", "disable") and len(args) >= 2:
            enabled = args[0] == "enable"
            if ctx.engine.set_skill_enabled(args[1], enabled):
                return f"Skill '{args[1]}' {'enabled' if enabled else 'disabled'}."
            return f"Skill '{args[1]}' not found."

        skills = ctx.engine.skills
        if not skills:
            return "No skills loaded."
        lines = [
            f"- {s.name} {'[disabled]' if not s.enabled else ''}: {s.description}"
            for s in skills
        ]
        return "Available skills:\n" + "\n".join(lines)


class PermissionCommand(_SimpleCommand):
    _name = "permission"
    _description = "Show or set permission mode (default/accept_edits/plan/bypass)"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Permission: (no engine attached)"
        from nono_agent.permissions import PermissionMode

        policy = ctx.engine.permission_policy
        if args:
            try:
                mode = PermissionMode(args[0])
            except ValueError:
                return f"Unknown mode '{args[0]}'. Options: {[m.value for m in PermissionMode]}"
            policy.set_mode(mode)
            return f"Permission mode set to '{mode.value}'."
        return f"Permission mode: {policy.mode.value}"


class McpCommand(_SimpleCommand):
    _name = "mcp"
    _description = "List configured MCP servers"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        from pathlib import Path

        from nono_agent.mcp.config import load_mcp_config

        servers = load_mcp_config(Path(ctx.cwd) / ".nono" / "mcp.json")
        if not servers:
            return "No MCP servers configured (.nono/mcp.json)."
        lines = [
            f"- {name}: {s.transport}" + (f" ({s.command})" if s.command else "")
            for name, s in servers.items()
        ]
        return "MCP servers:\n" + "\n".join(lines)


class PluginsCommand(_SimpleCommand):
    _name = "plugins"
    _description = "List loaded plugins"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        from nono_agent.plugins import FileSystemPluginLoader

        manifests = FileSystemPluginLoader().load(ctx.cwd)
        if not manifests:
            return "No plugins loaded."
        lines = [f"- {m.name} v{m.version}: {m.description}" for m in manifests]
        return "Plugins:\n" + "\n".join(lines)


class MemoryCommand(_SimpleCommand):
    _name = "memory"
    _description = "List persistent memories"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Memory: (no engine attached)"
        memories = ctx.engine.memory_store.load()
        if not memories:
            return "No memories stored."
        lines = [
            f"- [{m.kind}·{m.status}] {m.content}"
            + (f"  (agent={m.agent_id})" if m.agent_id else "")
            for m in memories
        ]
        return "Memories:\n" + "\n".join(lines)


class CompactCommand(_SimpleCommand):
    _name = "compact"
    _description = "Manually compact the conversation context"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Compact: (no engine attached)"
        removed = ctx.engine.compact()
        return f"Compacted: removed {removed} message(s)."


class HooksCommand(_SimpleCommand):
    _name = "hooks"
    _description = "List user hooks"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        from pathlib import Path

        from nono_agent.hooks import UserHooksConfig

        config = UserHooksConfig.from_file(Path(ctx.cwd) / ".nono" / "hooks.json")
        if not config.hooks:
            return "No user hooks configured (.nono/hooks.json)."
        lines = [
            f"- {h.event} {h.matcher or '*'} -> {h.command}" for h in config.hooks
        ]
        return "User hooks:\n" + "\n".join(lines)


def create_builtin_commands(registry: object | None = None) -> list[SlashCommand]:
    """创建内置 Slash 命令列表。"""
    commands: list[SlashCommand] = [
        ModelCommand(),
        TokensCommand(),
        ContextCommand(),
        ClearCommand(),
        CompactCommand(),
        RulesCommand(),
        SkillsCommand(),
        PermissionCommand(),
        McpCommand(),
        PluginsCommand(),
        MemoryCommand(),
        HooksCommand(),
    ]
    commands.append(HelpCommand(registry))
    return commands
