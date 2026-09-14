"""用户级模型目录的读写 —— 不把密钥返回给前端。"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field

from nono_agent.providers.base import CANONICAL_ADAPTERS
from nono_agent.providers.catalog import (
    USER_CONFIG_PATH,
    AgentDefaultModel,
    LlmCatalog,
    ModelEntry,
    NamedProvider,
    api_key_env_name,
    load_catalog,
    lookup_api_key,
    save_credential,
    save_user_catalog,
)


class ProviderUpsert(BaseModel):
    id: str
    display_name: str
    api: str
    base_url: str | None = None
    api_key: str | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)


class DefaultModelBody(BaseModel):
    provider: str
    model: str


def public_catalog(cwd: str) -> dict[str, Any]:
    """合并后的目录快照，含 has_key，不含密钥。"""
    catalog = load_catalog(cwd)
    default = catalog.agent_default_model
    providers = []
    for pid, named in catalog.providers.items():
        providers.append(
            {
                "id": pid,
                "display_name": named.display_name,
                "api": named.api,
                "base_url": named.base_url,
                "api_key_env": named.api_key_env,
                "has_key": bool(lookup_api_key(named.api_key_env)),
                "models": [
                    {"id": item.id, "name": item.name, "context_window": item.context_window}
                    for item in named.models
                ],
            }
        )
    return {
        "adapters": list(CANONICAL_ADAPTERS),
        "default": (
            {"provider": default.provider, "model": default.model} if default else None
        ),
        "providers": providers,
    }


def _load_user_catalog() -> LlmCatalog:
    if not USER_CONFIG_PATH.exists():
        return LlmCatalog()
    raw = yaml.safe_load(USER_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        return LlmCatalog()
    return LlmCatalog.model_validate(raw)


def upsert_provider(body: ProviderUpsert) -> LlmCatalog:
    if body.api not in CANONICAL_ADAPTERS:
        raise ValueError(f"Unknown api '{body.api}'. Available: {list(CANONICAL_ADAPTERS)}")
    pid = body.id.strip()
    if not pid:
        raise ValueError("provider id is required")
    models: list[ModelEntry] = []
    for item in body.models:
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        models.append(ModelEntry(id=mid, name=item.get("name")))
    if not models:
        raise ValueError("at least one model id is required")

    catalog = _load_user_catalog()
    env_name = api_key_env_name(pid)
    existing = catalog.providers.get(pid)
    if existing is not None:
        env_name = existing.api_key_env
    catalog.llm_provider.providers[pid] = NamedProvider(
        display_name=body.display_name.strip() or pid,
        api_key_env=env_name,
        api=body.api,
        base_url=body.base_url or None,
        models=models,
    )
    if catalog.agent_default_model is None:
        catalog.agent_default_model = AgentDefaultModel(provider=pid, model=models[0].id)
    if body.api_key:
        save_credential(env_name, body.api_key.strip())
    save_user_catalog(catalog)
    return catalog


def set_default_model(body: DefaultModelBody) -> LlmCatalog:
    catalog = _load_user_catalog()
    if body.provider not in catalog.providers:
        merged = load_catalog(".")
        if body.provider not in merged.providers:
            raise ValueError(f"Unknown provider '{body.provider}'")
        named = merged.providers[body.provider]
        catalog.llm_provider.providers[body.provider] = named
    named = catalog.providers[body.provider]
    if not any(item.id == body.model for item in named.models):
        raise ValueError(f"Unknown model '{body.model}' on provider '{body.provider}'")
    catalog.agent_default_model = AgentDefaultModel(
        provider=body.provider, model=body.model
    )
    save_user_catalog(catalog)
    return catalog
