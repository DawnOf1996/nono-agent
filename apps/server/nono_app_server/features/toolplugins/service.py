"""工具插件目录的读取、开关与凭据写入。

密钥只写 ``~/.nono-agent/credentials``，不落 settings.json，也不经任何
读接口回显 —— 读接口只回答「配没配」。
"""

from __future__ import annotations

import re
from typing import Any

from nono_agent.paths import resolve_session_cwd
from nono_agent.providers.catalog import (
    USER_CREDENTIALS_PATH,
    lookup_api_key,
    save_credential,
)
from nono_agent.settings import load_settings_from_env, patch_project_settings
from nono_agent.toolplugins.config_schema import (
    ConfigSchemaError,
    get_option,
    parse_config_schema,
    set_option,
)
from nono_agent.toolplugins.runtime import api_key_env_for, discover_plugins, option_defaults
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.toolplugins.schemas import (
    ToolPluginApiKey,
    ToolPluginUpsert,
)

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# 一眼就是密钥的 option 键 —— 不允许从 settings 端点写进来。
_SECRET_OPTION = re.compile(r"(api[-_]?key|secret|password|token|credential)", re.I)


def validate_plugin_id(plugin_id: str) -> str:
    pid = plugin_id.strip()
    if not _PLUGIN_ID.match(pid):
        raise ValueError("id 需为小写字母开头，只能含小写字母、数字、下划线和连字符")
    return pid


def _live_statuses(cwd: str, manager: SessionManager) -> dict[str, Any]:
    resolved = resolve_session_cwd(cwd)
    for engine in manager.sessions.values():
        if resolve_session_cwd(engine._config.cwd) != resolved:
            continue
        runtime = engine.tool_plugin_runtime
        if runtime is None:
            continue
        return {status.id: status for status in runtime.statuses}
    return {}


def public_tool_plugins(cwd: str, manager: SessionManager) -> dict[str, Any]:
    """工具插件目录 + 每个插件的生效状态 + 可渲染的配置面板 schema。"""
    settings = load_settings_from_env(cwd)
    live = _live_statuses(cwd, manager)
    items: list[dict[str, Any]] = []

    for manifest, source in discover_plugins(cwd):
        if not manifest.tools:
            continue
        block = settings.get(f"tools.{manifest.name}")
        block = block if isinstance(block, dict) else {}
        raw_options = block.get("options")
        options = (
            {str(k): v for k, v in raw_options.items()}
            if isinstance(raw_options, dict)
            else {}
        )
        api_key_env = str(block.get("api_key_env", "")).strip() or api_key_env_for(manifest)
        status = live.get(manifest.name)
        schema = parse_config_schema(manifest.config)
        defaults = option_defaults(manifest)

        items.append(
            {
                "id": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "source": source,
                "enabled": bool(block.get("enabled", False)),
                "api_key": {
                    "env": api_key_env,
                    "required": bool(api_key_env),
                    # 只回答"配没配"，永远不回显密钥本身。
                    "configured": bool(api_key_env and lookup_api_key(api_key_env)),
                    "label": schema.api_key_label,
                    "hint": schema.api_key_hint,
                    "placeholder": schema.api_key_placeholder,
                },
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "type": f.type,
                        "hint": f.hint,
                        "placeholder": f.placeholder,
                        "choices": [{"value": v, "label": lb} for v, lb in f.choices],
                        # 现值 = 用户配置，缺省回落到工具声明里的 defaults。
                        # 注意 defaults 里也可能是嵌套的（{"server": {...}}），
                        # 所以取值和写入都得走点分路径。
                        "value": get_option(options, f.key, get_option(defaults, f.key)),
                    }
                    for f in schema.fields
                    if not f.secret
                ],
                "tools": list(getattr(status, "tools", []) or []),
                "running": bool(getattr(status, "running", False)),
                "error": getattr(status, "error", None),
            }
        )

    return {
        "enabled": bool(settings.get("tools.enabled", True)),
        "credentials_path": str(USER_CREDENTIALS_PATH),
        "plugins": items,
    }


async def apply_upsert(
    plugin_id: str,
    body: ToolPluginUpsert,
    manager: SessionManager,
) -> dict[str, Any]:
    """写开关与 options，并刷新同工作区的活跃会话。"""
    pid = validate_plugin_id(plugin_id)
    cwd = (body.cwd or "").strip()
    manifest = _find_plugin(cwd, pid)
    block: dict[str, Any] = {}
    if body.enabled is not None:
        block["enabled"] = body.enabled
    if body.options is not None:
        by_key = {
            f.key: f
            for f in parse_config_schema(manifest.config if manifest else {}).fields
            if not f.secret
        }
        options: dict[str, Any] = {}
        for key, value in body.options.items():
            name = str(key)
            if _SECRET_OPTION.search(name):
                raise ValueError(
                    f"option '{name}' looks like a secret; use the api-key endpoint"
                )
            spec = by_key.get(name)
            if spec is not None:
                # 声明过的字段按 schema 收敛类型（数字框传进来的是字符串）
                try:
                    set_option(options, name, spec.coerce(value))
                except ConfigSchemaError as exc:
                    raise ValueError(str(exc)) from exc
                continue
            # 没在 schema 里声明的键照原样存 —— CLI / 手改配置的用法不该被面板卡住
            set_option(options, name, value)
        block["options"] = options

    if cwd and block:
        patch_project_settings(cwd, {"tools": {pid: block}})
        await manager.reload_extensions_for_cwd(cwd, mcp=False, tool_plugins=True)
    return public_tool_plugins(cwd, manager)


async def save_api_key(
    plugin_id: str,
    body: ToolPluginApiKey,
    manager: SessionManager,
) -> dict[str, Any]:
    """写入/清除凭据并刷新活跃会话；**不回显密钥**。"""
    pid = validate_plugin_id(plugin_id)
    cwd = (body.cwd or "").strip()
    settings = load_settings_from_env(cwd)
    block = settings.get(f"tools.{pid}")
    manifest = _find_plugin(cwd, pid)
    # settings 里没写过就回落到清单里的指针 —— 新装的插件第一次配密钥时
    # settings 里还没有任何东西，之前的写法会直接报"没有 api_key_env"。
    api_key_env = (
        str(block.get("api_key_env", "")).strip()
        if isinstance(block, dict)
        else ""
    ) or (api_key_env_for(manifest) if manifest is not None else "")
    if not api_key_env:
        raise ValueError(f"plugin '{pid}' has no api_key_env configured")

    save_credential(api_key_env, body.api_key.strip())
    if cwd:
        await manager.reload_extensions_for_cwd(cwd, mcp=False, tool_plugins=True)
    payload = public_tool_plugins(cwd, manager)
    for item in payload["plugins"]:
        if item["id"] == pid:
            item["api_key"]["configured"] = bool(lookup_api_key(api_key_env))
    return payload


def _find_plugin(cwd: str, plugin_id: str) -> Any:
    for manifest, _ in discover_plugins(cwd):
        if manifest.name == plugin_id:
            return manifest
    return None
