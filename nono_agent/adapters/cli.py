"""CLI 适配器 —— 本地终端消费 agent 流。

使用 Typer 解析命令行参数，Rich 渲染终端输出。
直接消费 submit_message() 的 AsyncGenerator，实时渲染到终端。

这是“客户端集成” —— 流在同一进程内本地消费，不通过网络推送。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from nono_agent.adapters.onboarding import (
    ADAPTER_CHOICES,
    USER_CONFIG_PATH,
    is_base_configured,
    load_user_config,
    save_user_config,
)
from nono_agent.core.config import EngineConfig, ProviderConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import (
    SDKAssistantMessage,
    SDKResultMessage,
    SDKStreamEventMessage,
    SDKSystemMessage,
)
from nono_agent.observability.token_tracker import (
    context_usage_color,
    format_context_usage,
)
from nono_agent.tools.context import PermissionChecker

logger = logging.getLogger(__name__)


def _make_cli_permission_checker(console: Any) -> PermissionChecker:
    """创建终端交互式权限检查器 —— 每次写操作前向用户确认。"""

    def checker(tool_name: str, input_params: dict[str, Any]) -> bool:
        # 截断过长参数避免终端刷屏
        params_str = str(input_params)
        if len(params_str) > 200:
            params_str = params_str[:200] + "..."

        console.print(
            f"\n[yellow][!] Tool '{tool_name}' requests write access.[/yellow]\n"
            f"[dim]  params: {params_str}[/dim]"
        )
        answer = console.input("[bold yellow]  Allow? [y/N]: [/bold yellow]").strip().lower()
        return answer in ("y", "yes")

    return checker


def _make_cli_ask_user(console: Any) -> Any:
    """创建终端交互式 AskUserQuestion 回调 —— 渲染问题并读取答案。"""

    def ask_user(questions: list[dict[str, Any]]) -> dict[str, Any]:
        answers: dict[str, Any] = {}
        for q in questions:
            header = q.get("header") or "Question"
            text = q.get("question", "")
            options = q.get("options") or []
            multi = bool(q.get("multi_select", False))

            console.print(f"\n[bold cyan]{header}[/bold cyan]")
            console.print(f"[bold]{text}[/bold]")
            if options:
                for i, opt in enumerate(options, start=1):
                    desc = opt.get("description", "")
                    suffix = f" [dim]- {desc}[/dim]" if desc else ""
                    console.print(f"  {i}. {opt.get('label', '?')}{suffix}")
                prompt = (
                    "[yellow]  Select numbers (comma-separated):[/yellow] "
                    if multi
                    else "[yellow]  Select a number:[/yellow] "
                )
                raw = console.input(prompt).strip()
                if multi:
                    chosen = [
                        options[int(n) - 1].get("label", "")
                        for n in raw.split(",")
                        if n.strip().isdigit() and 1 <= int(n) <= len(options)
                    ]
                else:
                    chosen = (
                        options[int(raw) - 1].get("label", "")
                        if raw.isdigit() and 1 <= int(raw) <= len(options)
                        else raw
                    )
            else:
                chosen = console.input("[yellow]  Your answer:[/yellow] ").strip()

            answers[text] = chosen
        return {"answers": answers}

    return ask_user


def format_token_summary(usage: Any) -> str:
    """把 Usage 渲染成一致的 token 摘要（四类 + 总计）。

    与 ``/tokens`` 命令（``TokensCommand``）口径一致：完整列出 input / output /
    cache_read / cache_creation 与总计，避免只显示三项导致数值与实际总用量对不上。
    """
    return (
        f"in={usage.input_tokens} out={usage.output_tokens} "
        f"cache_read={usage.cache_read_input_tokens} "
        f"cache_creation={usage.cache_creation_input_tokens} "
        f"(total {usage.total_tokens})"
    )


def _render_text(content: Any) -> str:
    """从消息的 content 字段提取可渲染文本 —— 处理各种内容块类型。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '?')}]")
                elif block_type == "tool_result":
                    result_text = block.get("content", "")
                    if isinstance(result_text, str) and len(result_text) > 200:
                        result_text = result_text[:200] + "..."
                    parts.append(f"[Result: {result_text}]")
                elif block_type == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        parts.append(f"── Thinking ──\n{thinking_text}\n── End Thinking ──")
        return "\n".join(parts)
    return str(content)

async def run_cli(
    config: EngineConfig,
    initial_message: str | None = None,
    *,
    resume_session: str | None = None,
    interactive: bool = True,
) -> None:
    """运行 CLI 适配器 —— 交互式 REPL 或单轮模式。

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
        print("Error: 'rich' package is required for CLI adapter.", file=sys.stderr)
        print("Install with: pip install rich", file=sys.stderr)
        sys.exit(1)

    console = Console()

    # Windows GBK 终端兼容: 如果控制台编码不是 UTF-8，强制使用 UTF-8
    # 解决 em-dash (—) 等 Unicode 字符无法渲染的问题
    if sys.platform == "win32" and hasattr(console.file, "encoding"):
        if console.file.encoding and console.file.encoding.lower() not in ("utf-8", "utf8"):
            import io

            console = Console(
                file=io.TextIOWrapper(
                    console.file.buffer, encoding="utf-8", errors="replace"
                )
            )

    # 终端交互式权限检查器
    permission_checker = _make_cli_permission_checker(console)

    # 注册内置工具
    from nono_agent.tools.builtin import create_builtin_tools
    from nono_agent.tools.registry import ToolRegistry

    tool_registry = ToolRegistry()
    for tool in create_builtin_tools():
        tool_registry.register(tool)

    # 注册 Slash 命令（MVP）
    from nono_agent.commands import CommandRegistry, parse_slash
    from nono_agent.commands.builtin import create_builtin_commands

    command_registry = CommandRegistry()
    for command in create_builtin_commands(command_registry):
        command_registry.register(command)

    # 注册插件自定义 slash（数据驱动）
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
        ask_user=_make_cli_ask_user(console),
    ) as engine:
        # Resume session if requested
        if resume_session:
            messages = await engine.resume_session(resume_session)
            console.print(f"[dim]Resumed session {resume_session} "
                         f"({len(messages)} messages)[/dim]")

        if not interactive and initial_message:
            # Single-shot mode
            await _process_one_message(engine, initial_message, console)
            return

        # Interactive REPL mode
        console.print(Panel.fit(
            f"[bold green]Nono Agent[/bold green] — Session: {engine.session_id}\n"
            f"Model: {config.effective_model}\n"
            f"Type 'exit' or 'quit' to stop. Ctrl+C during streaming to abort.\n"
            f"Type /help for commands.",
            border_style="blue",
        ))

        while True:
            # Windows bash (Git Bash) 中 Ctrl+C 发送 SIGINT 到进程组，
            # OS 层面直接杀进程。用 SIG_IGN 阻止进程被杀，Ctrl+C 仅取消当前输入。
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

            # Slash 命令分流
            slash = parse_slash(user_input)
            if slash is not None:
                name, args = slash
                from nono_agent.commands import CommandContext

                output = command_registry.execute(
                    name, args, CommandContext(engine=engine, cwd=config.cwd)
                )
                console.print(f"[dim]{output}[/dim]")
                continue

            await _process_one_message(engine, user_input, console)


async def _process_one_message(
    engine: QueryEngine,
    user_input: str,
    console: Any,
) -> SDKResultMessage | None:
    """Process a single user message and render the response stream.

    Ctrl+C during streaming triggers engine.abort() for graceful shutdown
    of the current query, rather than killing the entire process.
    """

    # 注册 SIGINT 处理器: Ctrl+C → engine.abort() 而非杀进程
    # 使用 signal.signal() 兼容 Unix + Windows (含 Git Bash)
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
        return await _consume_stream(engine, user_input, console)
    except KeyboardInterrupt:
        if not aborted:
            aborted = True
            engine.abort("User pressed Ctrl+C")
            console.print("\n[yellow][!] Aborting current query...[/yellow]")
        return None
    finally:
        # 恢复为 SIG_IGN（回到输入提示符时仍然忽略 SIGINT）
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, signal.SIG_IGN)


async def _consume_stream(
    engine: QueryEngine,
    user_input: str,
    console: Any,
) -> SDKResultMessage | None:
    """消费 submit_message 流并渲染输出 —— 拆出以保持信号处理清晰。

    流式渲染策略：
    - 通过 SDKStreamEventMessage 接收 content_block_delta 事件
    - 对每个 text_delta 实时追加打印（打字机效果）
    - 流完成后通过 SDKAssistantMessage 做最终渲染（回退方案）
    """
    # 流式文本累积状态
    is_streaming = False      # 是否正在接收流式文本
    last_text = ""            # 上一次累积的文本（用于计算增量）
    current_blocks: list[dict[str, Any]] = []  # 当前正在累积的内容块
    has_thinking = False      # 是否出现过思考块

    async for sdk_msg in engine.submit_message(user_input):
        if isinstance(sdk_msg, SDKStreamEventMessage):
            # 处理流式增量事件 —— 实时渲染文本
            event = sdk_msg.event
            event_type = event.get("type", "")

            if event_type == "content_block_start":
                block = event.get("content_block", {})
                if isinstance(block, dict):
                    current_blocks.append({**block})
                    block_type = block.get("type", "")
                    if block_type == "thinking":
                        has_thinking = True
                        if not is_streaming:
                            is_streaming = True
                            console.print("\n[bold magenta]Assistant:[/bold magenta]")
                        console.print("[dim italic]── Start Thinking ──[/dim italic]")
                    elif block_type == "text" and has_thinking:
                        # 思考结束，正文开始 —— 视觉分隔
                        console.print()

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                idx = event.get("index", -1)
                if isinstance(delta, dict) and 0 <= idx < len(current_blocks):
                    _apply_delta(current_blocks[idx], delta)

                # 计算新增文本并追加打印
                new_text = _render_blocks(current_blocks)
                if new_text and new_text != last_text:
                    if not is_streaming:
                        is_streaming = True
                        console.print("\n[bold magenta]Assistant:[/bold magenta]")
                    # 只打印新增部分（打字机效果）
                    incremental = new_text[len(last_text):]
                    if incremental:
                        console.print(incremental, end="", highlight=False)
                    last_text = new_text

            elif event_type == "content_block_stop":
                block_idx = event.get("index", -1)
                if 0 <= block_idx < len(current_blocks):
                    if current_blocks[block_idx].get("type") == "thinking":
                        # 思考块结束 —— 打印结束标记
                        console.print("\n[dim]── End Thinking ──[/dim]")
                        last_text = _render_blocks(current_blocks)

        elif isinstance(sdk_msg, SDKAssistantMessage):
            # 完整消息 —— 如果流式已渲染则跳过，否则打印
            text = _render_text(sdk_msg.message.get("content", ""))
            if text:
                if not is_streaming:
                    # 没有流式事件到达，回退到一次性打印
                    console.print(f"\n[bold magenta]Assistant:[/bold magenta]\n{text}")
                else:
                    # 流式渲染完成，打印一个换行结束
                    console.print()

        elif isinstance(sdk_msg, SDKSystemMessage):
            subtype = sdk_msg.subtype
            if subtype == "api_retry":
                console.print("[yellow][!] Retrying...[/yellow]")
            elif subtype == "tool_result":
                # 工具执行完成通知 —— 显示工具名称和耗时
                content = getattr(sdk_msg, "content", None) or ""
                if content:
                    console.print(f"[dim]  {content}[/dim]")
            elif subtype == "loop_terminal":
                pass
            elif subtype == "request_start":
                # 新一轮 API 调用 —— 重置流式状态，避免多轮块累积导致输出混乱
                current_blocks = []
                last_text = ""
                is_streaming = False
                has_thinking = False
                console.print("[dim]~ Waiting...[/dim]")

        elif isinstance(sdk_msg, SDKResultMessage):
            # Print result summary
            token_str = format_token_summary(sdk_msg.usage)
            if sdk_msg.is_error:
                console.print(
                    f"\n[red]Error: {sdk_msg.subtype}[/red]\n"
                    f"Duration: {sdk_msg.duration_ms:.0f}ms | "
                    f"Turns: {sdk_msg.num_turns} | "
                    f"Tokens: {token_str}"
                )
            else:
                console.print(
                    f"\n[dim][OK] Completed in {sdk_msg.duration_ms:.0f}ms | "
                    f"{sdk_msg.num_turns} turns | "
                    f"{token_str}[/dim]"
                )
            # 上下文用量行 —— 占比条颜色随占用率变化（<50% 绿 / <80% 黄 / ≥80% 红）
            ctx_usage = sdk_msg.context_usage
            ctx_line = format_context_usage(ctx_usage)
            if ctx_line:
                color = context_usage_color(float(ctx_usage.get("usage_ratio", 0.0)))
                console.print(f"[{color}]{ctx_line}[/{color}]")
            return sdk_msg

    return None


def _apply_delta(block: dict[str, Any], delta: dict[str, Any]) -> None:
    """将流式 delta 合并到内容块中。"""
    delta_type = delta.get("type", "")
    if delta_type == "text_delta":
        block["text"] = block.get("text", "") + delta.get("text", "")
    elif delta_type == "input_json_delta":
        # input 在流式开始时可能是空 dict，需转为字符串再累积
        current = block.get("input", "")
        if isinstance(current, dict):
            current = ""
        block["input"] = current + delta.get("partial_json", "")
    elif delta_type == "thinking_delta":
        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")


def _render_blocks(blocks: list[dict[str, Any]]) -> str:
    """从内容块列表渲染可显示的增量文本。

    注意：流式路径不包含 thinking 的视觉标记（由事件处理器控制），
    避免增量计算时标记残留在输出中间。
    """
    parts = []
    for block in blocks:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            name = block.get("name", "?")
            parts.append(f"[Tool: {name}]")
        elif block_type == "thinking":
            thinking = block.get("thinking", "")
            if thinking:
                parts.append(thinking)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI 配置外部化 —— 从环境变量 / 配置文件读取，禁止硬编码密钥
# ---------------------------------------------------------------------------

# 环境变量名（优先级从高到低由 resolve 逻辑决定）
ENV_MODEL = "NONO_MODEL"
ENV_MODEL_CONTEXT = "NONO_MODEL_CONTEXT"
ENV_ADAPTER = "NONO_ADAPTER"
ENV_API_KEY = "NONO_API_KEY"
ENV_BASE_URL = "NONO_BASE_URL"
ENV_CWD = "NONO_CWD"


def _read_config_file(path: str) -> dict[str, Any]:
    """读取 JSON 配置文件 —— 文件不存在或损坏时返回空字典。"""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _coalesce(*values: Any) -> Any:
    """返回第一个非 None / 非空字符串的值。"""
    for v in values:
        if v is not None and v != "":
            return v
    return None


def run_onboarding(console: Any) -> dict[str, Any]:
    """交互式引导 —— 收集协议 / 模型 / 密钥 / 端点，返回基础配置字典。"""
    from rich.panel import Panel

    console.print(Panel.fit(
        "[bold green]欢迎使用 Nono Agent[/bold green]\n"
        "首次运行需要配置模型与 API 凭据，配置将保存到 "
        f"[cyan]{USER_CONFIG_PATH}[/cyan]。",
        border_style="blue",
    ))

    # 1. 协议（adapter）
    console.print("\n[bold]① 选择协议 (adapter)：[/bold]")
    for i, choice in enumerate(ADAPTER_CHOICES, start=1):
        console.print(f"  {i}. {choice}")
    raw = console.input("[yellow]请输入序号（默认 1 = openai）：[/yellow] ").strip()
    adapter = (
        ADAPTER_CHOICES[int(raw) - 1]
        if raw.isdigit() and 1 <= int(raw) <= len(ADAPTER_CHOICES)
        else "openai"
    )

    # 2. 模型
    model = (
        console.input("[yellow]② 模型 (默认 MiniMax-M3)：[/yellow] ").strip()
        or "MiniMax-M3"
    )

    # 3. 密钥（必填）
    api_key = console.input("[yellow]③ API Key（必填）：[/yellow] ").strip()
    while not api_key:
        api_key = console.input("[yellow]API Key 不能为空，请重新输入：[/yellow] ").strip()

    # 4. 端点（可选）
    base_url = console.input("[yellow]④ Base URL（可留空）：[/yellow] ").strip()

    provider: dict[str, Any] = {"adapter": adapter, "api_key": api_key}
    if base_url:
        provider["base_url"] = base_url
    return {"model": model, "provider": provider}


def build_engine_config(
    *,
    model: str | None = None,
    cwd: str | None = None,
    verbose: bool = False,
    config_file: str | None = None,
) -> EngineConfig:
    """从 CLI 参数 + 环境变量 + 配置文件 + 用户基础配置构建 EngineConfig。

    优先级：命令行参数 > 环境变量 > --config 文件 > 用户基础配置 > 默认值。
    密钥只允许来自环境变量或配置文件，绝不硬编码。
    """
    file_cfg: dict[str, Any] = _read_config_file(config_file) if config_file else {}
    # 合并用户级基础配置（~/.nono-agent/config.json），--config 优先
    merged_cfg: dict[str, Any] = {**load_user_config(), **file_cfg}

    # 模型：CLI > env > 配置文件 > 默认
    resolved_model = _coalesce(model, os.environ.get(ENV_MODEL), merged_cfg.get("model"))
    resolved_model = resolved_model or "claude-sonnet-4-20250514"

    # cwd：CLI > env > 配置文件 > 默认
    resolved_cwd = _coalesce(cwd, os.environ.get(ENV_CWD), merged_cfg.get("cwd"))
    resolved_cwd = resolved_cwd or "."

    # Provider 配置：配置文件 provider 段 + 环境变量覆盖
    file_provider = merged_cfg.get("provider") or {}
    if not isinstance(file_provider, dict):
        file_provider = {}

    api_key = _coalesce(
        os.environ.get(ENV_API_KEY),
        file_provider.get("api_key"),
    )
    base_url = _coalesce(
        os.environ.get(ENV_BASE_URL),
        file_provider.get("base_url"),
    )
    adapter = _coalesce(
        os.environ.get(ENV_ADAPTER),
        file_provider.get("adapter"),
    )
    # 模型上下文窗口（token 数）：env > provider 段 > 顶层（--config 文件 / config.json）
    model_context = _coalesce(
        os.environ.get(ENV_MODEL_CONTEXT),
        file_provider.get("model_context"),
        merged_cfg.get("model_context"),
    )

    provider: ProviderConfig | None = None
    if adapter or api_key or base_url:
        provider = ProviderConfig(
            model_id=resolved_model,
            model_context=model_context,  # None 时由引擎级/默认值兜底
            adapter=adapter,
            api_key=api_key or "",
            base_url=base_url,
            extra_body=file_provider.get("extra_body") or {},
        )

    return EngineConfig(
        model=resolved_model,
        cwd=resolved_cwd,
        model_context=model_context,
        verbose=verbose,
        provider=provider,
        include_partial_messages=True,  # 启用流式事件，实现实时文本渲染
    )


def create_cli_app() -> Any:
    """Create a Typer CLI application for the agent.

    Returns a Typer app that can be run with:
        python -m nono_agent.adapters.cli
    """
    try:
        import typer
    except ImportError:
        print("Error: 'typer' package is required.", file=sys.stderr)
        sys.exit(1)

    app = typer.Typer(help="Nono Agent — AI Agent Engine")

    @app.command()
    def chat(
        model: str = typer.Option(None, help="Model to use (default from env/config)"),
        cwd: str = typer.Option(None, help="Working directory (default: env/config or '.')"),
        resume: str = typer.Option(None, help="Session ID to resume"),
        message: str = typer.Option(
            None, "--message", "-m", help="Single message (non-interactive)"
        ),
        config_file: str = typer.Option(None, "--config", "-c", help="JSON config file path"),
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    ) -> None:
        """Start an interactive chat session with the agent.

        Provider credentials are read from environment variables (NONO_API_KEY,
        NONO_BASE_URL, NONO_ADAPTER, NONO_MODEL, NONO_CWD) or a --config JSON file.
        No API keys are hardcoded.
        """
        import logging

        # 日志配置: 默认 WARNING 级别，--verbose 时开启 DEBUG
        logging.basicConfig(
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            level=logging.DEBUG if verbose else logging.WARNING,
        )
        # 抑制第三方库的冗余日志
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        # 引导页：交互模式且缺少必备配置（协议 + 密钥）时执行引导
        if message is None and not is_base_configured(config_file):
            from rich.console import Console

            console = Console()
            base_cfg = run_onboarding(console)
            save_user_config(base_cfg)
            console.print(
                f"[green]✓ 配置已保存到 {USER_CONFIG_PATH}[/green]"
            )

        config = build_engine_config(
            model=model,
            cwd=cwd,
            verbose=verbose,
            config_file=config_file,
        )

        asyncio.run(
            run_cli(
                config,
                initial_message=message,
                resume_session=resume,
                interactive=message is None,
            )
        )

    @app.command()
    def sessions() -> None:
        """列出本地已保存的会话（session id + 事件数）。"""
        from nono_agent.session import list_session_files

        sessions = list_session_files()
        if not sessions:
            print("No sessions found.")
            return
        for s in sessions:
            print(f"{s['session_id']}  ({s['event_count']} events)")

    return app


if __name__ == "__main__":
    app = create_cli_app()
    app()
