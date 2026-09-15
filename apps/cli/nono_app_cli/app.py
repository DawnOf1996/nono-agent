"""Typer 入口 —— chat / sessions 命令。"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from nono_app_cli.catalog import ensure_catalog_ready
from nono_app_cli.config import build_engine_config, resolve_resume_bindings
from nono_app_cli.repl import run_cli


def create_cli_app() -> Any:
    """Create a Typer CLI application for the agent.

    Returns a Typer app that can be run with:
        python -m nono_app_cli
    """
    try:
        import typer
    except ImportError:
        print("Error: 'typer' package is required.", file=sys.stderr)
        sys.exit(1)

    app = typer.Typer(help="Nono Agent — AI Agent Engine")

    @app.command()
    def chat(
        model: str = typer.Option(None, help="Model id (default from catalog / NONO_MODEL)"),
        provider: str = typer.Option(None, help="Provider id from the YAML catalog"),
        cwd: str = typer.Option(None, help="Working directory (default: env/config or '.')"),
        resume: str = typer.Option(
            None,
            help="Session ID to resume (cwd/summary come from that session's .header.json)",
        ),
        message: str = typer.Option(
            None, "--message", "-m", help="Single message (non-interactive)"
        ),
        config_file: str = typer.Option(
            None, "--config", "-c", help="YAML catalog overlay path"
        ),
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
        log_api_requests: bool = typer.Option(
            False,
            "--log-api-requests",
            help="Log the SDK request payload immediately before each LLM HTTP call",
        ),
    ) -> None:
        """Start an interactive chat session with the agent.

        LLM endpoints come from ~/.nono-agent/config.yaml (and project overlay).
        API keys are stored in ~/.nono-agent/credentials and referenced by api_key_env.
        """
        from rich.console import Console

        logging.basicConfig(
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            level=logging.DEBUG if verbose else logging.WARNING,
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        console = Console()
        resolved_cwd, session_id = resolve_resume_bindings(resume, cwd)
        ensure_catalog_ready(
            console,
            cwd=resolved_cwd,
            config_file=config_file,
            interactive=message is None,
        )

        config = build_engine_config(
            model=model,
            provider=provider,
            cwd=resolved_cwd,
            verbose=verbose,
            config_file=config_file,
            log_api_requests=log_api_requests,
        )
        if config.log_api_requests:
            from nono_agent.providers._request_log import enable_api_request_logging

            enable_api_request_logging()
        if session_id:
            config = config.model_copy(update={"session_id": session_id})

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

        items = list_session_files()
        if not items:
            print("No sessions found.")
            return
        for s in items:
            summary = s.get("summary") or ""
            cwd_label = s.get("cwd") or ""
            extras = "  ".join(part for part in (summary, cwd_label) if part)
            suffix = f"  {extras}" if extras else ""
            print(f"{s['session_id']}  ({s['event_count']} events){suffix}")

    return app


def main() -> None:
    """``python -m nono_app_cli`` / ``nono`` 入口。"""
    create_cli_app()()
