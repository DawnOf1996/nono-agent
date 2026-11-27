"""首次运行时确保用户级 LLM 目录可用。"""

from __future__ import annotations

import os
import sys
from typing import Any

from nono_agent.providers.catalog import load_catalog, save_credential, save_user_catalog
from nono_app_cli.config import ENV_CWD, coalesce
from nono_app_cli.onboarding import (
    catalog_present,
    is_base_configured,
    prompt_api_key,
    run_onboarding,
)


def ensure_catalog_ready(
    console: Any,
    *,
    cwd: str | None,
    config_file: str | None,
    interactive: bool,
) -> None:
    """空目录则引导；已有 YAML 但缺密钥时只补 credentials，不覆盖目录。"""
    from nono_agent.providers import catalog as catalog_mod

    resolved_cwd = coalesce(cwd, os.environ.get(ENV_CWD)) or "."
    if is_base_configured(resolved_cwd, overlay_path=config_file):
        return

    if catalog_present(resolved_cwd, overlay_path=config_file):
        catalog = load_catalog(resolved_cwd, overlay_path=config_file)
        try:
            catalog.resolve()
            return
        except ValueError as exc:
            if not interactive:
                console.print(f"[red]{exc}[/red]")
                sys.exit(1)
            default = catalog.agent_default_model
            named = (
                catalog.providers.get(default.provider) if default is not None else None
            )
            if named is None:
                console.print(f"[red]{exc}[/red]")
                sys.exit(1)
            console.print(
                f"[yellow]目录已存在，但缺少密钥 {named.api_key_env}。[/yellow]"
            )
            key = prompt_api_key(console, env_name=named.api_key_env)
            save_credential(named.api_key_env, key)
            creds = catalog_mod.USER_CREDENTIALS_PATH
            console.print(f"[green]✓ 密钥已保存到 {creds}[/green]")
            return

    if not interactive:
        console.print(
            "[red]No LLM catalog configured. "
            "Run interactive `chat` to create ~/.nono-agent/config.yaml.[/red]"
        )
        sys.exit(1)

    catalog, api_key = run_onboarding(console)
    save_user_catalog(catalog)
    env_name = next(iter(catalog.providers.values())).api_key_env
    save_credential(env_name, api_key)
    dest = catalog_mod.USER_CONFIG_PATH
    creds = catalog_mod.USER_CREDENTIALS_PATH
    console.print(f"[green]✓ 目录已保存到 {dest}[/green]")
    console.print(f"[green]✓ 密钥已保存到 {creds}（YAML 中仅保留 {env_name}）[/green]")
