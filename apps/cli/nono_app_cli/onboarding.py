"""CLI 引导配置 —— 用户级 LLM 目录（YAML）的交互收集。

用户级目录保存在 ``~/.nono-agent/config.yaml``。密钥写入
``~/.nono-agent/credentials``（0600），YAML 只记录自动生成的 ``api_key_env``。
"""

from __future__ import annotations

import json
import re
from typing import Any

from nono_agent.providers.base import CANONICAL_ADAPTERS
from nono_agent.providers.catalog import (
    USER_CONFIG_PATH,
    AgentDefaultModel,
    LlmCatalog,
    LlmProviderSection,
    ModelEntry,
    NamedProvider,
    api_key_env_name,
    catalog_present,
    is_base_configured,
    parse_token_size,
    save_user_catalog,
)

# 用户可见协议：按 API 形态分三类
ADAPTER_CHOICES = list(CANONICAL_ADAPTERS)

__all__ = [
    "ADAPTER_CHOICES",
    "USER_CONFIG_PATH",
    "catalog_present",
    "is_base_configured",
    "prompt_api_key",
    "run_onboarding",
    "save_user_catalog",
]


def _title_segments(value: str) -> str:
    """按 ``-`` 分段，每段首字母大写（``xx-xx`` → ``Xx-Xx``）。"""
    pieces = re.split(r"(-)", value)
    titled: list[str] = []
    for piece in pieces:
        if piece in ("-", ""):
            titled.append(piece)
        else:
            titled.append(piece[:1].upper() + piece[1:])
    return "".join(titled)


def _read_optional_json_object(
    console: Any,
    prompt: str,
    *,
    stringify_values: bool = False,
) -> dict[str, Any]:
    """读取可选 JSON 对象。留空返回 {}；非法 JSON / 非对象则重试。"""
    while True:
        raw = str(console.input(prompt)).strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            console.print("[red]不是合法 JSON，请重新输入或留空。[/red]")
            continue
        if not isinstance(data, dict):
            console.print('[red]必须是 JSON 对象，例如 {"k":"v"}。[/red]')
            continue
        if stringify_values:
            return {str(k): str(v) for k, v in data.items()}
        return {str(k): v for k, v in data.items()}


def _read_optional_int(console: Any, prompt: str) -> int | None:
    while True:
        raw = str(console.input(prompt)).strip()
        if not raw:
            return None
        parsed = parse_token_size(raw)
        if parsed is None:
            console.print(
                "[red]请输入正整数，或带 k/K、m/M 后缀（如 128K、1M）。[/red]"
            )
            continue
        return parsed


def prompt_api_key(console: Any, *, env_name: str | None = None) -> str:
    """读取真实 API Key。``env_name`` 仅用于提示指针名。"""
    hint = f"（将保存为 {env_name}）" if env_name else ""
    api_key = str(
        console.input(f"[yellow]API Key{hint}（必填）：[/yellow] ")
    ).strip()
    while not api_key:
        api_key = str(
            console.input("[yellow]API Key 不能为空，请重新输入：[/yellow] ")
        ).strip()
    return api_key


def run_onboarding(console: Any) -> tuple[LlmCatalog, str]:
    """交互式引导 —— 收集供应商 / 协议 / 模型与真实 key。

    返回 ``(catalog, api_key)``。catalog 不含密钥，只含自动生成的 ``api_key_env``。
    """
    from rich.panel import Panel

    from nono_agent.providers import catalog as catalog_mod

    dest = catalog_mod.USER_CONFIG_PATH
    creds = catalog_mod.USER_CREDENTIALS_PATH
    console.print(Panel.fit(
        "[bold green]欢迎使用 Nono Agent[/bold green]\n"
        "首次运行需要配置 LLM 供应商目录，目录保存到 "
        f"[cyan]{dest}[/cyan]。\n"
        f"API Key 保存到 [cyan]{creds}[/cyan]（不写入 YAML）。",
        border_style="blue",
    ))

    provider_id = console.input("[yellow]① 供应商 id(如openai)：[/yellow] ").strip()
    while not provider_id:
        provider_id = console.input(
            "[yellow]供应商 id 不能为空，请重新输入：[/yellow] "
        ).strip()
    default_display = _title_segments(provider_id)
    display_name = (
        console.input(
            f"[yellow]② 显示名称（默认 {default_display}）：[/yellow] "
        ).strip()
        or default_display
    )

    console.print("\n[bold]③ 选择协议 (api)：[/bold]")
    for i, choice in enumerate(ADAPTER_CHOICES, start=1):
        console.print(f"  {i}. {choice}")
    default_api = ADAPTER_CHOICES[0]
    raw = console.input(
        f"[yellow]请输入序号（默认 1 = {default_api}）：[/yellow] "
    ).strip()
    api = (
        ADAPTER_CHOICES[int(raw) - 1]
        if raw.isdigit() and 1 <= int(raw) <= len(ADAPTER_CHOICES)
        else default_api
    )

    base_url = console.input("[yellow]④ Base URL（可留空）：[/yellow] ").strip() or None
    extra_headers = _read_optional_json_object(
        console,
        '[yellow]⑤ extra_headers JSON（可留空，例 {"X-Foo":"bar"}）：[/yellow] ',
        stringify_values=True,
    )
    extra_body = _read_optional_json_object(
        console,
        '[yellow]⑥ extra_body JSON（可留空，例 {"reasoning_split":true}）：[/yellow] ',
    )

    env_name = api_key_env_name(provider_id)
    console.print("[bold]⑦ API Key：[/bold]")
    console.print(f"[dim]密钥指针：{env_name}[/dim]")
    api_key = prompt_api_key(console, env_name=env_name)

    model_id = console.input("[yellow]⑧ 模型 id（发给 API）：[/yellow] ").strip()
    while not model_id:
        model_id = console.input(
            "[yellow]模型 id 不能为空，请重新输入：[/yellow] "
        ).strip()
    default_model_name = _title_segments(model_id)
    model_name = (
        console.input(
            f"[yellow]⑨ 模型展示名（默认 {default_model_name}）：[/yellow] "
        ).strip()
        or default_model_name
    )
    context_window = _read_optional_int(
        console, "[yellow]⑩ context_window（可留空，如 1M）：[/yellow] "
    )
    max_tokens = _read_optional_int(
        console, "[yellow]⑪ max_tokens（可留空，如 128K）：[/yellow] "
    )

    named = NamedProvider(
        display_name=display_name,
        api_key_env=env_name,
        api=api,
        base_url=base_url,
        extra_headers=extra_headers,
        extra_body=extra_body,
        models=[
            ModelEntry(
                id=model_id,
                name=model_name,
                context_window=context_window,
                max_tokens=max_tokens,
            )
        ],
    )
    catalog = LlmCatalog(
        agent_default_model=AgentDefaultModel(provider=provider_id, model=model_id),
        llm_provider=LlmProviderSection(providers={provider_id: named}),
    )
    return catalog, api_key
