"""Tests for CLI onboarding of the YAML LLM catalog."""

from __future__ import annotations

from nono_agent.providers.catalog import (
    AgentDefaultModel,
    LlmCatalog,
    LlmProviderSection,
    ModelEntry,
    NamedProvider,
    catalog_present,
    load_catalog,
    save_user_catalog,
)
from nono_app_cli import onboarding as ob


class _FakeConsole:
    def __init__(self, answers: list[str]) -> None:
        self._answers = iter(answers)
        self.output: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.output.append(" ".join(str(a) for a in args))

    def input(self, prompt: str = "", **kwargs: object) -> str:
        return next(self._answers)


class TestAdapterChoices:
    def test_three_protocol_names(self):
        assert ob.ADAPTER_CHOICES == [
            "openai_chat_completions",
            "openai_responses",
            "anthropic_messages",
        ]


class TestIsBaseConfigured:
    def test_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("A_API_KEY", raising=False)
        monkeypatch.delenv("NONO_A_API_KEY", raising=False)
        assert ob.is_base_configured(tmp_path) is False
        assert catalog_present(tmp_path) is False

    def test_yaml_without_secret_is_present_but_not_ready(self, tmp_path, monkeypatch):
        monkeypatch.delenv("A_API_KEY", raising=False)
        monkeypatch.delenv("NONO_A_API_KEY", raising=False)
        save_user_catalog(
            LlmCatalog(
                agent_default_model=AgentDefaultModel(provider="a", model="m"),
                llm_provider=LlmProviderSection(
                    providers={
                        "a": NamedProvider(
                            display_name="A",
                            api_key_env="A_API_KEY",
                            api="openai_chat_completions",
                            models=[ModelEntry(id="m")],
                        )
                    }
                ),
            )
        )
        assert catalog_present(tmp_path) is True
        assert ob.is_base_configured(tmp_path) is False

    def test_yaml_and_env_ready(self, tmp_path, monkeypatch):
        save_user_catalog(
            LlmCatalog(
                agent_default_model=AgentDefaultModel(provider="a", model="m"),
                llm_provider=LlmProviderSection(
                    providers={
                        "a": NamedProvider(
                            display_name="A",
                            api_key_env="A_API_KEY",
                            api="openai_chat_completions",
                            models=[ModelEntry(id="m")],
                        )
                    }
                ),
            )
        )
        monkeypatch.setenv("A_API_KEY", "sk")
        assert ob.is_base_configured(tmp_path) is True

    def test_yaml_and_credentials_file_ready(self, tmp_path, monkeypatch):
        from nono_agent.providers.catalog import save_credential

        save_user_catalog(
            LlmCatalog(
                agent_default_model=AgentDefaultModel(provider="a", model="m"),
                llm_provider=LlmProviderSection(
                    providers={
                        "a": NamedProvider(
                            display_name="A",
                            api_key_env="A_API_KEY",
                            api="openai_chat_completions",
                            models=[ModelEntry(id="m")],
                        )
                    }
                ),
            )
        )
        save_credential("A_API_KEY", "sk-file")
        monkeypatch.delenv("A_API_KEY", raising=False)
        assert ob.is_base_configured(tmp_path) is True


class TestParseTokenSize:
    def test_suffixes(self):
        from nono_agent.providers.catalog import parse_token_size

        assert parse_token_size("1M") == 1_000_000
        assert parse_token_size("1m") == 1_000_000
        assert parse_token_size("128K") == 128_000
        assert parse_token_size("128k") == 128_000
        assert parse_token_size("1000000") == 1_000_000
        assert parse_token_size("1_000") == 1_000
        assert parse_token_size("") is None
        assert parse_token_size("abc") is None

    def test_model_entry_coerces_suffix_to_int(self):
        from nono_agent.providers.catalog import ModelEntry, dump_yaml

        entry = ModelEntry(id="m", context_window="1M", max_tokens="128K")
        assert entry.context_window == 1_000_000
        assert entry.max_tokens == 128_000
        dumped = dump_yaml(entry.model_dump(exclude_none=True))
        assert "1000000" in dumped
        assert "128000" in dumped
        assert "1M" not in dumped
        assert "128K" not in dumped


class TestOnboardingRoundTrip:
    def test_run_onboarding_writes_yaml_without_key(self, tmp_path, monkeypatch):
        console = _FakeConsole(
            [
                "a",
                "My-Provider",
                "2",  # openai_responses
                "https://api.minimax.cn/v1",
                "",
                "",
                "sk-secret",
                "MiniMax-M3",
                "MiniMax-M31",
                "1000000",
                "128000",
                "y",  # ⑫ 支持图片输入
            ]
        )
        catalog, api_key = ob.run_onboarding(console)
        save_user_catalog(catalog)
        from nono_agent.providers import catalog as catalog_mod
        from nono_agent.providers.catalog import save_credential

        save_credential(catalog.providers["a"].api_key_env, api_key)
        text = catalog_mod.USER_CONFIG_PATH.read_text(encoding="utf-8")
        assert "api_key:" not in text
        assert "sk-secret" not in text
        assert "api_key_env: NONO_A_API_KEY" in text
        creds = catalog_mod.USER_CREDENTIALS_PATH.read_text(encoding="utf-8")
        assert "NONO_A_API_KEY=sk-secret" in creds
        monkeypatch.delenv("NONO_A_API_KEY", raising=False)
        loaded = load_catalog(tmp_path)
        assert loaded.agent_default_model is not None
        assert loaded.agent_default_model.provider == "a"
        assert loaded.agent_default_model.model == "MiniMax-M3"
        named = loaded.providers["a"]
        assert named.api_key_env == "NONO_A_API_KEY"
        assert named.api == "openai_responses"
        assert named.base_url == "https://api.minimax.cn/v1"
        assert named.models[0].name == "MiniMax-M31"
        assert named.models[0].context_window == 1_000_000
        assert named.models[0].max_tokens == 128_000
        assert named.models[0].input_types == ["text", "image"]
        assert loaded.resolve().api_key == "sk-secret"

    def test_extra_headers_and_body_json(self, tmp_path, monkeypatch):
        console = _FakeConsole(
            [
                "a",
                "",
                "1",
                "",
                '{"X-Foo":"bar"}',
                '{"reasoning_split":true}',
                "sk-secret",
                "m",
                "",
                "",
                "",
                "",  # ⑫ 默认不勾图片
            ]
        )
        catalog, _api_key = ob.run_onboarding(console)
        named = catalog.providers["a"]
        assert named.extra_headers == {"X-Foo": "bar"}
        assert named.extra_body == {"reasoning_split": True}
        assert named.models[0].input_types == ["text"]

    def test_display_defaults_title_segments(self):
        console = _FakeConsole(
            [
                "my-provider",
                "",
                "1",
                "",
                "",
                "",
                "sk",
                "mini-max-m3",
                "",
                "",
                "",
                "",  # ⑫ 默认不勾图片
            ]
        )
        catalog, _api_key = ob.run_onboarding(console)
        named = catalog.providers["my-provider"]
        assert named.display_name == "My-Provider"
        assert named.models[0].name == "Mini-Max-M3"

    def test_token_size_suffixes(self):
        console = _FakeConsole(
            [
                "a",
                "",
                "1",
                "",
                "",
                "",
                "sk",
                "m",
                "",
                "1M",
                "128K",
                "y",  # ⑫ 支持图片输入
            ]
        )
        catalog, _api_key = ob.run_onboarding(console)
        save_user_catalog(catalog)
        model = catalog.providers["a"].models[0]
        assert model.context_window == 1_000_000
        assert model.max_tokens == 128_000
        assert model.input_types == ["text", "image"]
        from nono_agent.providers import catalog as catalog_mod

        text = catalog_mod.USER_CONFIG_PATH.read_text(encoding="utf-8")
        assert "context_window: 1000000" in text
        assert "max_tokens: 128000" in text
        assert "1M" not in text
        assert "128K" not in text
