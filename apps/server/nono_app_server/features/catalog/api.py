"""模型目录 HTTP。"""

from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from nono_app_server.common.deps import get_sidecar
from nono_app_server.config import SidecarSettings
from nono_app_server.features.catalog.schemas import (
    DefaultModelBody,
    DiscoverModelsBody,
    ProviderUpsert,
)
from nono_app_server.features.catalog.service import (
    delete_provider,
    discover_remote_models,
    open_user_config,
    public_catalog,
    set_default_model,
    upsert_provider,
)

router = APIRouter()


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
