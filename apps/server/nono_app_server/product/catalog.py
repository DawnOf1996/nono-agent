"""用户级模型目录：读写服务 + /app/catalog 路由。不把密钥返回给前端。"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nono_agent.providers import catalog as catalog_mod
from nono_agent.providers.base import CANONICAL_ADAPTERS
from nono_agent.providers.catalog import (
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
from nono_app_server.config import SidecarSettings
from nono_app_server.product.deps import get_sidecar

router = APIRouter()


class ProviderUpsert(BaseModel):
    id: str
    display_name: str
    api: str
    base_url: str | None = None
    api_key: str | None = None
    extra_headers: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)


class DefaultModelBody(BaseModel):
    provider: str
    model: str


class DiscoverModelsBody(BaseModel):
    base_url: str
    api: str = "openai_chat_completions"
    api_key: str | None = None
    provider_id: str | None = None


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
                "extra_headers": named.extra_headers,
                "extra_body": named.extra_body,
                "models": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "context_window": item.context_window,
                        "max_tokens": item.max_tokens,
                    }
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
        "config_path": str(catalog_mod.USER_CONFIG_PATH),
    }


def _load_user_catalog() -> LlmCatalog:
    path = catalog_mod.USER_CONFIG_PATH
    if not path.exists():
        return LlmCatalog()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        return LlmCatalog()
    return LlmCatalog.model_validate(raw)


def _parse_models(raw: list[dict[str, Any]]) -> list[ModelEntry]:
    models: list[ModelEntry] = []
    for item in raw:
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        name = item.get("name")
        if isinstance(name, str):
            name = name.strip() or None
        try:
            models.append(
                ModelEntry(
                    id=mid,
                    name=name,
                    context_window=item.get("context_window") or None,
                    max_tokens=item.get("max_tokens") or None,
                )
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"模型 '{mid}' 参数无效") from exc
    return models


def _coerce_extra_headers(raw: dict[str, Any] | None) -> dict[str, str] | None:
    if raw is None:
        return None
    headers: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, (dict, list)):
            raise ValueError("extra_headers 的值必须是字符串")
        headers[name] = str(value)
    return headers


def _coerce_extra_body(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    return {str(key): value for key, value in raw.items() if str(key).strip()}


def upsert_provider(body: ProviderUpsert) -> LlmCatalog:
    if body.api not in CANONICAL_ADAPTERS:
        raise ValueError(f"Unknown api '{body.api}'. Available: {list(CANONICAL_ADAPTERS)}")
    pid = body.id.strip()
    if not pid:
        raise ValueError("provider id is required")
    models = _parse_models(body.models)
    if not models:
        raise ValueError("at least one model id is required")

    catalog = _load_user_catalog()
    env_name = api_key_env_name(pid)
    existing = catalog.providers.get(pid)
    extra_headers = _coerce_extra_headers(body.extra_headers)
    extra_body = _coerce_extra_body(body.extra_body)
    if extra_headers is None:
        extra_headers = existing.extra_headers if existing is not None else {}
    if extra_body is None:
        extra_body = existing.extra_body if existing is not None else {}
    timeout = existing.timeout if existing is not None else None
    if existing is not None:
        env_name = existing.api_key_env
    catalog.llm_provider.providers[pid] = NamedProvider(
        display_name=body.display_name.strip() or pid,
        api_key_env=env_name,
        api=body.api,
        base_url=body.base_url or None,
        models=models,
        extra_headers=extra_headers,
        extra_body=extra_body,
        timeout=timeout,
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


def delete_provider(provider_id: str) -> LlmCatalog:
    pid = provider_id.strip()
    catalog = _load_user_catalog()
    if pid not in catalog.providers:
        raise ValueError(f"Unknown provider '{pid}'")
    del catalog.llm_provider.providers[pid]
    default = catalog.agent_default_model
    if default is not None and default.provider == pid:
        remaining = next(iter(catalog.providers.items()), None)
        if remaining is None:
            catalog.agent_default_model = None
        else:
            next_id, named = remaining
            fallback = named.models[0].id if named.models else default.model
            catalog.agent_default_model = AgentDefaultModel(
                provider=next_id, model=fallback
            )
    save_user_catalog(catalog)
    return catalog


def _discover_api_key(body: DiscoverModelsBody) -> str:
    key = (body.api_key or "").strip()
    if key:
        return key
    pid = (body.provider_id or "").strip()
    if not pid:
        raise ValueError("需要 API 密钥才能获取模型列表")
    catalog = _load_user_catalog()
    named = catalog.providers.get(pid)
    if named is None:
        merged = load_catalog(".")
        named = merged.providers.get(pid)
    if named is None:
        raise ValueError("需要 API 密钥才能获取模型列表")
    stored = lookup_api_key(named.api_key_env)
    if not stored:
        raise ValueError("需要 API 密钥才能获取模型列表")
    return stored


def discover_remote_models(body: DiscoverModelsBody) -> list[dict[str, str]]:
    base = (body.base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("API 地址不能为空")
    api_key = _discover_api_key(body)
    headers = {"Authorization": f"Bearer {api_key}"}
    if body.api == "anthropic_messages":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    url = f"{base}/models"
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"获取模型失败（HTTP {exc.response.status_code}）"
        ) from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"获取模型失败：{exc}") from exc
    except ValueError as exc:
        raise ValueError(f"获取模型失败：{exc}") from exc

    raw_items: list[Any]
    if isinstance(payload, dict):
        raw_items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        mid: str | None = None
        name: str | None = None
        if isinstance(item, str):
            mid = item.strip()
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
            raw_name = item.get("display_name") or item.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                name = raw_name.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        models.append({"id": mid, "name": name or mid})
    models.sort(key=lambda item: item["id"].lower())
    return models


def open_user_config() -> dict[str, Any]:
    dest = catalog_mod.USER_CONFIG_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_text("", encoding="utf-8")
    target = str(dest)
    try:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    return {"path": target, "opened": True}


@router.get("/catalog")
async def get_catalog(
    settings: SidecarSettings = Depends(get_sidecar),
) -> dict[str, Any]:
    return public_catalog(settings.cwd)


@router.post("/catalog/providers")
async def save_provider(
    body: ProviderUpsert,
    settings: SidecarSettings = Depends(get_sidecar),
) -> dict[str, Any]:
    try:
        upsert_provider(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_catalog(settings.cwd)


@router.delete("/catalog/providers/{provider_id}")
async def remove_provider(
    provider_id: str,
    settings: SidecarSettings = Depends(get_sidecar),
) -> dict[str, Any]:
    try:
        delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_catalog(settings.cwd)


@router.post("/catalog/default")
async def save_default(
    body: DefaultModelBody,
    settings: SidecarSettings = Depends(get_sidecar),
) -> dict[str, Any]:
    try:
        set_default_model(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_catalog(settings.cwd)


@router.post("/catalog/models")
async def list_remote_models(
    body: DiscoverModelsBody,
) -> dict[str, Any]:
    try:
        models = discover_remote_models(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"models": models}


@router.post("/catalog/open-config")
async def open_config() -> dict[str, Any]:
    try:
        return open_user_config()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
