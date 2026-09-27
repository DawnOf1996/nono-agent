"""Tests for the product-layer catalog service (settings / custom providers)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import yaml

from nono_agent.providers.catalog import (
    AgentDefaultModel,
    LlmCatalog,
    LlmProviderSection,
    ModelEntry,
    NamedProvider,
    save_user_catalog,
)
from nono_app_server.features.catalog.schemas import (
    DefaultModelBody,
    DiscoverModelsBody,
    ProviderUpsert,
)
from nono_app_server.features.catalog.service import (
    delete_provider,
    discover_remote_models,
    public_catalog,
    set_default_model,
    upsert_provider,
)


def _named(**kwargs: object) -> NamedProvider:
    defaults: dict[str, object] = dict(
        display_name="Acme",
        api_key_env="NONO_ACME_API_KEY",
        api="openai_chat_completions",
        base_url="https://gateway.example/v1",
        models=[
            ModelEntry(id="acme-large", name="Large", context_window=256_000, max_tokens=32_000)
        ],
    )
    defaults.update(kwargs)
    return NamedProvider(**defaults)  # type: ignore[arg-type]


def _seed(tmp_path, monkeypatch, catalog: LlmCatalog) -> None:
    save_user_catalog(catalog)
    monkeypatch.chdir(tmp_path)


class TestUpsertProvider:
    def test_persists_models_and_token_sizes(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, LlmCatalog())
        upsert_provider(
            ProviderUpsert(
                id="acme-gateway",
                display_name="Acme Gateway",
                api="openai_chat_completions",
                base_url="https://gateway.example/v1",
                api_key="sk-test",
                models=[
                    {
                        "id": "acme-large",
                        "name": "Large",
                        "context_window": "256K",
                        "max_tokens": "32K",
                    }
                ],
            )
        )
        snap = public_catalog(str(tmp_path))
        assert snap["config_path"].endswith("config.yaml")
        provider = next(item for item in snap["providers"] if item["id"] == "acme-gateway")
        assert provider["display_name"] == "Acme Gateway"
        assert provider["has_key"] is True
        assert provider["models"][0]["context_window"] == 256_000
        assert provider["models"][0]["max_tokens"] == 32_000
        assert snap["default"] == {"provider": "acme-gateway", "model": "acme-large"}

    def test_preserves_extra_headers_on_update(self, tmp_path, monkeypatch):
        catalog = LlmCatalog(
            agent_default_model=AgentDefaultModel(provider="acme", model="m1"),
            llm_provider=LlmProviderSection(
                providers={
                    "acme": _named(
                        extra_headers={"X-Project": "1"},
                        extra_body={"foo": True},
                        models=[ModelEntry(id="m1")],
                    )
                }
            ),
        )
        _seed(tmp_path, monkeypatch, catalog)
        upsert_provider(
            ProviderUpsert(
                id="acme",
                display_name="Acme 2",
                api="openai_chat_completions",
                models=[{"id": "m2", "name": "Two"}],
            )
        )
        loaded = yaml.safe_load(
            (tmp_path / ".nono-agent" / "config.yaml").read_text(encoding="utf-8")
        )
        named = loaded["llm_provider"]["providers"]["acme"]
        assert named["display_name"] == "Acme 2"
        assert named["extra_headers"] == {"X-Project": "1"}
        assert named["extra_body"] == {"foo": True}
        assert named["models"][0]["id"] == "m2"

    def test_writes_extra_headers_and_body(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, LlmCatalog())
        upsert_provider(
            ProviderUpsert(
                id="minimax",
                display_name="MiniMax",
                api="openai_responses",
                extra_headers={"X-Foo": "bar"},
                extra_body={"reasoning_split": True},
                models=[{"id": "MiniMax-M3"}],
            )
        )
        snap = public_catalog(str(tmp_path))
        provider = next(item for item in snap["providers"] if item["id"] == "minimax")
        assert provider["extra_headers"] == {"X-Foo": "bar"}
        assert provider["extra_body"] == {"reasoning_split": True}

    def test_empty_extra_fields_clear_existing(self, tmp_path, monkeypatch):
        catalog = LlmCatalog(
            agent_default_model=AgentDefaultModel(provider="acme", model="m1"),
            llm_provider=LlmProviderSection(
                providers={
                    "acme": _named(
                        extra_headers={"X-Project": "1"},
                        extra_body={"foo": True},
                        models=[ModelEntry(id="m1")],
                    )
                }
            ),
        )
        _seed(tmp_path, monkeypatch, catalog)
        upsert_provider(
            ProviderUpsert(
                id="acme",
                display_name="Acme",
                api="openai_chat_completions",
                extra_headers={},
                extra_body={},
                models=[{"id": "m1"}],
            )
        )
        snap = public_catalog(str(tmp_path))
        provider = next(item for item in snap["providers"] if item["id"] == "acme")
        assert provider["extra_headers"] == {}
        assert provider["extra_body"] == {}

    def test_writes_model_extra_body(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, LlmCatalog())
        upsert_provider(
            ProviderUpsert(
                id="minimax",
                display_name="MiniMax",
                api="openai_responses",
                extra_body={"reasoning_split": True},
                models=[
                    {
                        "id": "MiniMax-M3",
                        "context_window": "1M",
                        "max_tokens": "32K",
                        "extra_body": {"reasoning": {"effort": "high"}},
                    }
                ],
            )
        )
        snap = public_catalog(str(tmp_path))
        provider = next(item for item in snap["providers"] if item["id"] == "minimax")
        assert provider["extra_body"] == {"reasoning_split": True}
        assert provider["models"][0]["context_window"] == 1_000_000
        assert provider["models"][0]["max_tokens"] == 32_000
        assert provider["models"][0]["extra_body"] == {"reasoning": {"effort": "high"}}


class TestDeleteProvider:
    def test_reassigns_default(self, tmp_path, monkeypatch):
        catalog = LlmCatalog(
            agent_default_model=AgentDefaultModel(provider="a", model="m1"),
            llm_provider=LlmProviderSection(
                providers={
                    "a": _named(
                        display_name="A",
                        api_key_env="NONO_A_API_KEY",
                        models=[ModelEntry(id="m1")],
                    ),
                    "b": _named(
                        display_name="B",
                        api_key_env="NONO_B_API_KEY",
                        models=[ModelEntry(id="m2")],
                    ),
                }
            ),
        )
        _seed(tmp_path, monkeypatch, catalog)
        delete_provider("a")
        snap = public_catalog(str(tmp_path))
        ids = [item["id"] for item in snap["providers"]]
        assert ids == ["b"]
        assert snap["default"] == {"provider": "b", "model": "m2"}

    def test_unknown_provider(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, LlmCatalog())
        with pytest.raises(ValueError, match="Unknown provider"):
            delete_provider("missing")


class TestDiscoverRemoteModels:
    def test_parses_openai_payload(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, LlmCatalog())
        payload = {"data": [{"id": "b-model"}, {"id": "a-model", "name": "Alpha"}]}

        class DummyResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return payload

        class DummyClient:
            def __enter__(self) -> DummyClient:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def get(self, url: str, headers: dict | None = None) -> DummyResponse:
                assert url == "https://gateway.example/v1/models"
                assert headers is not None
                assert headers["Authorization"] == "Bearer sk-form"
                return DummyResponse()

        with patch(
            "nono_app_server.features.catalog.service.httpx.Client",
            return_value=DummyClient(),
        ):
            models = discover_remote_models(
                DiscoverModelsBody(
                    base_url="https://gateway.example/v1",
                    api_key="sk-form",
                )
            )
        assert models == [
            {"id": "a-model", "name": "Alpha"},
            {"id": "b-model", "name": "b-model"},
        ]

    def test_http_error(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, LlmCatalog())
        request = httpx.Request("GET", "https://gateway.example/v1/models")
        response = httpx.Response(401, request=request)

        class DummyClient:
            def __enter__(self) -> DummyClient:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def get(self, url: str, headers: dict | None = None) -> httpx.Response:
                return response

        with patch(
            "nono_app_server.features.catalog.service.httpx.Client",
            return_value=DummyClient(),
        ):
            with pytest.raises(ValueError, match="HTTP 401"):
                discover_remote_models(
                    DiscoverModelsBody(
                        base_url="https://gateway.example/v1",
                        api_key="sk-bad",
                    )
                )


class TestSetDefaultStillWorks:
    def test_unknown_model(self, tmp_path, monkeypatch):
        catalog = LlmCatalog(
            agent_default_model=AgentDefaultModel(provider="acme", model="m1"),
            llm_provider=LlmProviderSection(
                providers={"acme": _named(models=[ModelEntry(id="m1")])}
            ),
        )
        _seed(tmp_path, monkeypatch, catalog)
        with pytest.raises(ValueError, match="Unknown model"):
            set_default_model(DefaultModelBody(provider="acme", model="missing"))
