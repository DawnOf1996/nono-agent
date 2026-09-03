"""Tests for the LLM provider YAML catalog."""

from __future__ import annotations

import pytest

from nono_agent.providers.catalog import (
    load_catalog,
    merge_catalog_dicts,
    save_user_catalog,
)

USER_YAML = """
agent_default_model:
  provider: a
  model: MiniMax-M3
llm_provider:
  providers:
    a:
      display_name: My-Provider
      api_key_env: A_API_KEY
      api: openai_responses
      base_url: https://api.minimax.cn/v1
      models:
        - id: MiniMax-M3
          name: MiniMax-M31
          context_window: 1000000
          max_tokens: 128000
        - id: other
          name: Other
    b:
      display_name: Other-Provider
      api_key_env: B_API_KEY
      api: openai_chat_completions
      models:
        - id: shared-id
          name: From-B
"""

PROJECT_YAML = """
agent_default_model:
  provider: a
  model: other
llm_provider:
  providers:
    a:
      display_name: Project-Provider
      extra_headers:
        X-Project: "1"
      models:
        - id: MiniMax-M3
          max_tokens: 64000
        - id: project-only
          name: Project Only
          context_window: 8000
"""


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestMergeCatalogDicts:
    def test_default_replaced_providers_deep_merged_models_by_id(self):
        merged = merge_catalog_dicts(
            {"agent_default_model": {"provider": "a", "model": "m1"},
             "llm_provider": {"providers": {
                 "a": {
                     "display_name": "A",
                     "api_key_env": "A_KEY",
                     "api": "openai_responses",
                     "models": [
                         {"id": "m1", "name": "One", "max_tokens": 100},
                         {"id": "m2", "name": "Two"},
                     ],
                 }
             }}},
            {"agent_default_model": {"provider": "a", "model": "m2"},
             "llm_provider": {"providers": {
                 "a": {
                     "display_name": "A-proj",
                     "models": [{"id": "m1", "max_tokens": 200}],
                 },
                 "c": {
                     "display_name": "C",
                     "api_key_env": "C_KEY",
                     "api": "anthropic_messages",
                     "models": [{"id": "m3"}],
                 },
             }}},
        )
        assert merged["agent_default_model"] == {"provider": "a", "model": "m2"}
        providers = merged["llm_provider"]["providers"]
        assert providers["a"]["display_name"] == "A-proj"
        assert providers["a"]["api_key_env"] == "A_KEY"
        models = {m["id"]: m for m in providers["a"]["models"]}
        assert models["m1"]["name"] == "One"
        assert models["m1"]["max_tokens"] == 200
        assert models["m2"]["name"] == "Two"
        assert "c" in providers


class TestLoadCatalog:
    def test_user_and_project_merge(self, tmp_path):
        from nono_agent.providers import catalog as catalog_mod

        user_path = catalog_mod.USER_CONFIG_PATH
        _write(user_path, USER_YAML)
        project = tmp_path / "proj"
        _write(project / ".nono-agent" / "config.yaml", PROJECT_YAML)

        catalog = load_catalog(project)
        assert catalog.agent_default_model is not None
        assert catalog.agent_default_model.model == "other"
        named = catalog.providers["a"]
        assert named.display_name == "Project-Provider"
        assert named.api == "openai_responses"
        assert named.extra_headers == {"X-Project": "1"}
        by_id = {m.id: m for m in named.models}
        assert by_id["MiniMax-M3"].name == "MiniMax-M31"
        assert by_id["MiniMax-M3"].max_tokens == 64000
        assert by_id["MiniMax-M3"].context_window == 1_000_000
        assert "project-only" in by_id
        assert "other" in by_id

    def test_overlay_wins(self, tmp_path):
        from nono_agent.providers import catalog as catalog_mod

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        overlay = tmp_path / "overlay.yaml"
        _write(
            overlay,
            """
agent_default_model:
  provider: a
  model: other
""",
        )
        catalog = load_catalog(tmp_path, overlay_path=overlay)
        assert catalog.agent_default_model is not None
        assert catalog.agent_default_model.model == "other"
        assert catalog.providers["a"].display_name == "My-Provider"


class TestResolve:
    def test_default_resolve(self, tmp_path, monkeypatch):
        from nono_agent.providers import catalog as catalog_mod

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        monkeypatch.setenv("A_API_KEY", "sk-a")
        pc = load_catalog(tmp_path).resolve()
        assert pc.adapter == "openai_responses"
        assert pc.model_id == "MiniMax-M3"
        assert pc.model_context == 1_000_000
        assert pc.max_tokens == 128_000
        assert pc.api_key == "sk-a"
        assert pc.base_url == "https://api.minimax.cn/v1"

    def test_model_only_uses_default_provider(self, tmp_path, monkeypatch):
        from nono_agent.providers import catalog as catalog_mod

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        monkeypatch.setenv("A_API_KEY", "sk-a")
        pc = load_catalog(tmp_path).resolve(model_id="other")
        assert pc.model_id == "other"
        assert pc.adapter == "openai_responses"

    def test_model_id_conflict_requires_provider(self, tmp_path, monkeypatch):
        from nono_agent.providers import catalog as catalog_mod

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        overlay = tmp_path / "overlay.yaml"
        _write(
            overlay,
            """
llm_provider:
  providers:
    c:
      display_name: C
      api_key_env: C_API_KEY
      api: anthropic_messages
      models:
        - id: shared-id
          name: From-C
""",
        )
        monkeypatch.setenv("A_API_KEY", "sk-a")
        monkeypatch.setenv("B_API_KEY", "sk-b")
        monkeypatch.setenv("C_API_KEY", "sk-c")
        catalog = load_catalog(tmp_path, overlay_path=overlay)
        with pytest.raises(ValueError, match="multiple providers"):
            catalog.resolve(model_id="shared-id")
        pc = catalog.resolve(provider_id="b", model_id="shared-id")
        assert pc.adapter == "openai_chat_completions"

    def test_missing_secret_raises(self, tmp_path, monkeypatch):
        from nono_agent.providers import catalog as catalog_mod

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        monkeypatch.delenv("A_API_KEY", raising=False)
        with pytest.raises(ValueError, match="No API key"):
            load_catalog(tmp_path).resolve()

    def test_resolve_from_credentials_file(self, tmp_path, monkeypatch):
        from nono_agent.providers import catalog as catalog_mod
        from nono_agent.providers.catalog import save_credential

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        save_credential("A_API_KEY", "sk-from-file")
        monkeypatch.delenv("A_API_KEY", raising=False)
        pc = load_catalog(tmp_path).resolve()
        assert pc.api_key == "sk-from-file"

    def test_env_overrides_credentials_file(self, tmp_path, monkeypatch):
        from nono_agent.providers import catalog as catalog_mod
        from nono_agent.providers.catalog import save_credential

        _write(catalog_mod.USER_CONFIG_PATH, USER_YAML)
        save_credential("A_API_KEY", "sk-from-file")
        monkeypatch.setenv("A_API_KEY", "sk-from-env")
        pc = load_catalog(tmp_path).resolve()
        assert pc.api_key == "sk-from-env"

    def test_unknown_api(self, tmp_path):
        from nono_agent.providers import catalog as catalog_mod

        _write(
            catalog_mod.USER_CONFIG_PATH,
            """
agent_default_model:
  provider: a
  model: m
llm_provider:
  providers:
    a:
      display_name: X
      api_key_env: X_KEY
      api: not_a_protocol
      models:
        - id: m
""",
        )
        catalog = load_catalog(tmp_path)
        with pytest.raises(ValueError, match="Unknown api"):
            catalog.resolve()

    def test_empty_catalog(self, tmp_path):
        with pytest.raises(ValueError, match="agent_default_model"):
            load_catalog(tmp_path).resolve()


class TestApiKeyEnvName:
    def test_simple_id(self):
        from nono_agent.providers.catalog import api_key_env_name

        assert api_key_env_name("a") == "NONO_A_API_KEY"

    def test_sanitizes_and_is_stable(self):
        from nono_agent.providers.catalog import api_key_env_name

        assert api_key_env_name("my-provider") == "NONO_MY_PROVIDER_API_KEY"
        assert api_key_env_name("my-provider") == api_key_env_name("my-provider")
        assert api_key_env_name("1foo") == "NONO_P_1FOO_API_KEY"


class TestCredentialsFile:
    def test_upsert_preserves_other_keys(self, monkeypatch):
        from nono_agent.providers.catalog import load_credentials, save_credential

        save_credential("NONO_A_API_KEY", "sk-a")
        save_credential("NONO_B_API_KEY", "sk-b")
        save_credential("NONO_A_API_KEY", "sk-a-new")
        monkeypatch.delenv("NONO_A_API_KEY", raising=False)
        monkeypatch.delenv("NONO_B_API_KEY", raising=False)
        creds = load_credentials()
        assert creds["NONO_A_API_KEY"] == "sk-a-new"
        assert creds["NONO_B_API_KEY"] == "sk-b"


class TestSaveRoundTrip:
    def test_save_and_load(self, tmp_path, monkeypatch):
        from nono_agent.providers.catalog import (
            AgentDefaultModel,
            LlmCatalog,
            LlmProviderSection,
            ModelEntry,
            NamedProvider,
        )

        catalog = LlmCatalog(
            agent_default_model=AgentDefaultModel(provider="a", model="MiniMax-M3"),
            llm_provider=LlmProviderSection(
                providers={
                    "a": NamedProvider(
                        display_name="My-Provider",
                        api_key_env="A_API_KEY",
                        api="openai_responses",
                        base_url="https://api.minimax.cn/v1",
                        models=[
                            ModelEntry(
                                id="MiniMax-M3",
                                name="MiniMax-M31",
                                context_window=1_000_000,
                                max_tokens=128_000,
                            )
                        ],
                    )
                }
            ),
        )
        save_user_catalog(catalog)
        monkeypatch.setenv("A_API_KEY", "sk")
        loaded = load_catalog(tmp_path)
        dumped = loaded.to_yaml_dict()
        assert "api_key" not in dumped
        assert "api_key" not in dumped.get("llm_provider", {}).get("providers", {}).get("a", {})
        pc = loaded.resolve()
        assert pc.model_id == "MiniMax-M3"
        assert pc.max_tokens == 128_000
