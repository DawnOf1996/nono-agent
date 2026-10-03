"""交互式 REPL 与单轮消息处理。"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import SDKResultMessage
from nono_app_cli.interactive import (
    make_cli_ask_user,
    make_cli_permission_checker,
    print_mcp_startup_feedback,
)
from nono_app_cli.render import consume_stream

logger = logging.getLogger(__name__)


async def run_cli(
    config: EngineConfig,
    initial_message: str | None = None,
    *,
    resume_session: str | None = None,
    interactive: bool = True,
) -> None:
    """运行 CLI —— 交互式 REPL 或单轮模式。

    Args:
        config: 引擎配置
        initial_message: 可选的首条消息（非交互模式）
        resume_session: 要恢复的会话 ID
        interactive: 是否启用交互式 REPL 模式
    """
    try:
        from rich.console import Console
        from rich.panel import Panel
    except ImportError:
        print("Error: 'rich' package is required for CLI.", file=sys.stderr)
        print("Install with: pip install rich", file=sys.stderr)
        sys.exit(1)

    console = Console()

    if sys.platform == "win32" and hasattr(console.file, "encoding"):
        if console.file.encoding and console.file.encoding.lower() not in ("utf-8", "utf8"):
            import io

            console = Console(
                file=io.TextIOWrapper(
                    console.file.buffer, encoding="utf-8", errors="replace"
                )
            )

    permission_checker = make_cli_permission_checker(console)

    from nono_agent.tools.builtin import create_builtin_tools
    from nono_agent.tools.registry import ToolRegistry

    tool_registry = ToolRegistry()
    for tool in create_builtin_tools(config):
        tool_registry.register(tool)

    from nono_agent.commands import CommandRegistry, parse_slash
    from nono_agent.commands.builtin import create_builtin_commands

    command_registry = CommandRegistry()
    for command in create_builtin_commands(command_registry):
        command_registry.register(command)

    try:
        from nono_agent.plugins import FileSystemPluginLoader, activate_commands

        for manifest in FileSystemPluginLoader().load(config.cwd):
            for command in activate_commands(manifest):
                command_registry.register(command)
    except Exception:
        logger.warning("Failed to load plugin commands", exc_info=True)

    async with QueryEngine(
        config,
        permission_checker=permission_checker,
        tool_registry=tool_registry,
        ask_user=make_cli_ask_user(console),
    ) as engine:
        if resume_session:
            messages = await engine.resume_session(resume_session)
            console.print(
                f"[dim]Resumed session {resume_session} ({len(messages)} messages)[/dim]"
            )

        if not interactive and initial_message:
            print_mcp_startup_feedback(console, engine)
            await process_one_message(engine, initial_message, console)
            return

        console.print(
            Panel.fit(
                f"[bold green]Nono Agent[/bold green] — Session: {engine.session_id}\n"
                f"Model: {config.effective_model}\n"
                f"Type 'exit' or 'quit' to stop. Ctrl+C during streaming to abort.\n"
                f"Type /help for commands.",
                border_style="blue",
            )
        )
        print_mcp_startup_feedback(console, engine)

        while True:
            if hasattr(signal, "SIGINT"):
                signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                user_input = console.input("\n[bold cyan]You:[/bold cyan] ").strip()
            except EOFError:
                console.print("\n[dim]Goodbye![/dim]")
                break
            except KeyboardInterrupt:
                console.print()
                continue

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                console.print("[dim]Goodbye![/dim]")
                break

            slash = parse_slash(user_input)
            if slash is not None:
                name, args = slash
                from rich.markup import escape

                from nono_agent.commands import CommandContext

                output = command_registry.execute(
                    name, args, CommandContext(engine=engine, cwd=config.cwd)
                )
                console.print(f"[dim]{escape(output)}[/dim]")
                continue

            await process_one_message(engine, user_input, console)


async def process_one_message(
    engine: QueryEngine,
    user_input: str,
    console: Any,
) -> SDKResultMessage | None:
    """处理单条用户消息并渲染响应流。

    流式过程中 Ctrl+C 触发 engine.abort()，而不是杀掉整个进程。
    """
    loop = asyncio.get_running_loop()
    aborted = False

    def _on_sigint(signum: int, frame: Any) -> None:
        nonlocal aborted
        if not aborted:
            aborted = True
            engine.abort("User pressed Ctrl+C")
            loop.call_soon_threadsafe(
                lambda: console.print("\n[yellow][!] Aborting current query...[/yellow]")
            )

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _on_sigint)

    try:
        return await consume_stream(engine, user_input, console)
    except KeyboardInterrupt:
        if not aborted:
            aborted = True
            engine.abort("User pressed Ctrl+C")
            console.print("\n[yellow][!] Aborting current query...[/yellow]")
        return None
    finally:
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, signal.SIG_IGN)
