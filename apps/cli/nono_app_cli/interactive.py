"""终端交互：权限确认、AskUser 与 MCP 启动反馈。"""

from __future__ import annotations

from typing import Any

from nono_agent.core.engine import QueryEngine
from nono_agent.tools.context import PermissionChecker


def print_mcp_startup_feedback(console: Any, engine: QueryEngine) -> None:
    """引擎启动后立刻打印 MCP 失败，避免只在 /mcp 里才看到。"""
    runtime = getattr(engine, "mcp_runtime", None)
    if runtime is None or not getattr(runtime, "enabled", False):
        return
    statuses = list(getattr(runtime, "statuses", []) or [])
    failed = [
        status
        for status in statuses
        if getattr(status, "enabled", True) and not getattr(status, "running", False)
    ]
    if not failed:
        return
    from rich.markup import escape

    console.print("[yellow]MCP startup errors:[/yellow]")
    for status in failed:
        target = getattr(status, "url", None) or getattr(status, "command", None) or ""
        where = f" ({target})" if target else ""
        detail = getattr(status, "error", None) or "failed to start"
        console.print(
            f"  [red]{escape(str(status.name))}[/red]{escape(where)}: {escape(str(detail))}"
        )


def make_cli_permission_checker(console: Any) -> PermissionChecker:
    """创建终端交互式权限检查器 —— 每次写操作前向用户确认。"""

    def checker(tool_name: str, input_params: dict[str, Any]) -> bool:
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


def make_cli_ask_user(console: Any) -> Any:
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
