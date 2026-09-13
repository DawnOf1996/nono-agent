"""内置 Slash 命令（MVP 子集）：help/model/tokens/context/clear 等。"""

from __future__ import annotations

from nono_agent.commands.registry import CommandContext, SlashCommand
from nono_agent.observability.token_tracker import format_context_usage


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
    _description = "Show the current model and catalog (no runtime switch)"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Model: (no engine attached)"
        current = ctx.engine._config.effective_model
        lines: list[str] = []
        if args:
            lines.append(
                "Switching models at runtime is not supported; "
                "restart with --provider / --model."
            )
        try:
            from nono_agent.providers.catalog import load_catalog

            catalog = load_catalog(ctx.cwd or ctx.engine._config.cwd)
            listing = catalog.format_listing(current)
            if listing and listing != "Catalog: (empty)":
                lines.append(listing)
            else:
                lines.append(f"Current: {current}")
        except Exception:
            lines.append(f"Current: {current}")
        return "\n".join(lines)


class TokensCommand(_SimpleCommand):
    _name = "tokens"
    _description = "Show session token usage and context usage breakdown"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Tokens: (no engine attached)"
        tracker = ctx.engine.token_tracker
        usage = tracker.get_total_usage()
        lines = [
            f"Tokens: in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={usage.cache_read_input_tokens} "
            f"cache_creation={usage.cache_creation_input_tokens} "
            f"(total {usage.total_tokens})"
        ]
        # 最近一次上下文用量分析快照（每步 API 响应时随用量一起记录）
        ctx_line = format_context_usage(tracker.get_context_usage())
        if ctx_line:
            lines.append(ctx_line)
        return "\n".join(lines)


class ContextCommand(_SimpleCommand):
    _name = "context"
    _description = "Show context usage breakdown (system/tools/messages)"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if ctx.engine is None:
            return "Context: (no engine attached)"
        usage = ctx.engine.analyze_context_usage()
        pct = f"{usage['usage_ratio'] * 100:.1f}%"
        lines = [
            f"Context: {usage['total_tokens']:,} / {usage['context_window']:,} "
            f"tokens ({pct}), remaining {usage['remaining_tokens']:,}",
            f"- system: {usage['system_prompt_tokens']:,} tokens "
            f"({usage['system_prompt_ratio'] * 100:.1f}%)",
            f"- tools ({usage['tools_count']}): {usage['tools_tokens']:,} tokens "
            f"({usage['tools_ratio'] * 100:.1f}%)",
            f"- messages ({usage['message_count']}): {usage['messages_tokens']:,} "
            f"tokens ({usage['messages_ratio'] * 100:.1f}%)",
        ]
        if usage["is_auto_compact_threshold"]:
            lines.append("! auto-compact threshold reached (80%)")
        if usage["is_at_blocking_limit"]:
            lines.append("! blocking limit reached (95%)")
        return "\n".join(lines)


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
            if not policy.request_mode(mode):
                return f"Permission mode is already '{mode.value}'."
            return (
                f"Permission mode will switch to '{mode.value}' "
                "at the next turn or step."
            )
        current = f"Permission mode: {policy.mode.value}"
        pending = policy.pending_mode
        if pending is not None:
            current += (
                f"\nPending: {pending.value} (applies at next turn or step)"
            )
        return current


class McpCommand(_SimpleCommand):
    _name = "mcp"
    _description = "List configured MCP servers"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        from nono_agent.settings import load_settings_from_env

        settings = load_settings_from_env(ctx.cwd)
        enabled = bool(settings.get("mcp.enabled", True))
        lines = [f"MCP enabled: {'on' if enabled else 'off'}"]

        runtime = getattr(ctx.engine, "mcp_runtime", None) if ctx.engine is not None else None
        if runtime is not None:
            if not runtime.enabled:
                lines.append("Runtime not started (disabled by settings).")
                return "\n".join(lines)
            if not runtime.statuses:
                lines.append("No MCP servers configured (.nono-agent/mcp.json).")
                return "\n".join(lines)
            lines.append("MCP servers:")
            for status in runtime.statuses:
                if status.running:
                    state = "running"
                elif not status.enabled:
                    state = "disabled"
                else:
                    state = "error"
                detail = status.url or status.command or ""
                extra = f" ({detail})" if detail else ""
                tools = f", {status.tool_count} tools" if status.running else ""
                hidden = (
                    f", disabledTools={','.join(status.disabled_tools)}"
                    if status.disabled_tools
                    else ""
                )
                lines.append(
                    f"- {status.name}: {status.transport} [{state}{tools}{hidden}]{extra}"
                )
                if status.error:
                    lines.append(f"  error: {status.error}")
            return "\n".join(lines)

        from nono_agent.mcp.config import load_mcp_config
        from nono_agent.mcp.policy import resolve_server_policy
        from nono_agent.paths import project_dir

        mcp_path = project_dir(ctx.cwd) / "mcp.json"
        servers = load_mcp_config(mcp_path)
        if not servers:
            lines.append("No MCP servers configured (.nono-agent/mcp.json).")
            return "\n".join(lines)
        lines.append("MCP servers:")
        for name, server in servers.items():
            extra = ""
            if server.command:
                extra = f" ({server.command})"
            elif server.url:
                extra = f" ({server.url})"
            policy = resolve_server_policy(settings, name)
            state = "enabled" if policy.enabled else "disabled"
            hidden = (
                f", disabledTools={','.join(sorted(policy.disabled_tools))}"
                if policy.disabled_tools
                else ""
            )
            lines.append(
                f"- {name}: {server.normalized_transport} [{state}{hidden}]{extra}"
            )
        return "\n".join(lines)


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
        from nono_agent.hooks import UserHooksConfig
        from nono_agent.paths import project_dir

        config = UserHooksConfig.from_file(project_dir(ctx.cwd) / "hooks.json")
        if not config.hooks:
            return "No user hooks configured (.nono-agent/hooks.json)."
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
