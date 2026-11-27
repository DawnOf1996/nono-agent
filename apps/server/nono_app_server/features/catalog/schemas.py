"""请求体。"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    Field,
)


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
