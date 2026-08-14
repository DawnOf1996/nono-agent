"""Tests for the CLI onboarding config (user-level base config)."""

import json

import pytest

from nono_agent.adapters import onboarding as ob
from nono_agent.adapters.cli import build_engine_config


@pytest.fixture
def user_cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(ob, "USER_CONFIG_PATH", path)
    return path


def _clear_env(monkeypatch):
    for name in ("NONO_ADAPTER", "NONO_API_KEY", "NONO_BASE_URL", "NONO_MODEL"):
        monkeypatch.delenv(name, raising=False)


class TestLoadSave:
    def test_load_missing(self, user_cfg_path):
        assert ob.load_user_config() == {}

    def test_save_and_load(self, user_cfg_path):
        ob.save_user_config({"model": "m", "provider": {"adapter": "openai", "api_key": "k"}})
        cfg = ob.load_user_config()
        assert cfg["model"] == "m"
        assert cfg["provider"]["adapter"] == "openai"


class TestIsBaseConfigured:
    def test_not_configured(self, user_cfg_path, monkeypatch):
        _clear_env(monkeypatch)
        assert ob.is_base_configured() is False

    def test_configured_via_user_file(self, user_cfg_path, monkeypatch):
        _clear_env(monkeypatch)
        ob.save_user_config({"provider": {"adapter": "openai", "api_key": "k"}})
        assert ob.is_base_configured() is True

    def test_configured_via_env(self, user_cfg_path, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("NONO_ADAPTER", "anthropic")
        monkeypatch.setenv("NONO_API_KEY", "sk")
        assert ob.is_base_configured() is True

    def test_missing_key_not_configured(self, user_cfg_path, monkeypatch):
        _clear_env(monkeypatch)
        ob.save_user_config({"provider": {"adapter": "openai"}})  # 无 api_key
        assert ob.is_base_configured() is False


class TestResolveProviderFields:
    def test_env_overrides_user(self, user_cfg_path, monkeypatch):
        _clear_env(monkeypatch)
        ob.save_user_config({"provider": {"adapter": "openai", "api_key": "user-key"}})
        monkeypatch.setenv("NONO_API_KEY", "env-key")
        fields = ob.resolve_provider_fields()
        assert fields["api_key"] == "env-key"
        assert fields["adapter"] == "openai"


class TestBuildEngineConfigUsesUserConfig:
    def test_picks_up_user_config(self, user_cfg_path, monkeypatch):
        _clear_env(monkeypatch)
        ob.save_user_config(
            {"model": "MiniMax-M3", "provider": {"adapter": "openai", "api_key": "k"}}
        )
        config = build_engine_config()
        assert config.model == "MiniMax-M3"
        assert config.provider is not None
        assert config.provider.adapter == "openai"
        assert config.provider.api_key == "k"

    def test_cli_file_overrides_user(self, user_cfg_path, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        ob.save_user_config({"provider": {"adapter": "openai", "api_key": "user-key"}})
        cli_file = tmp_path / "cli.json"
        cli_file.write_text(
            json.dumps({"provider": {"adapter": "anthropic", "api_key": "cli-key"}})
        )
        config = build_engine_config(config_file=str(cli_file))
        assert config.provider.adapter == "anthropic"
        assert config.provider.api_key == "cli-key"
