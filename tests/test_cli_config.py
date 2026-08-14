"""Tests for CLI config externalization (env vars / config file)."""

import json

from nono_agent.adapters.cli import build_engine_config


class TestBuildEngineConfig:
    def test_defaults(self):
        config = build_engine_config()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.provider is None

    def test_env_vars(self, monkeypatch):
        monkeypatch.setenv("NONO_MODEL", "my-model")
        monkeypatch.setenv("NONO_API_KEY", "sk-test")
        monkeypatch.setenv("NONO_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("NONO_ADAPTER", "openai")

        config = build_engine_config()
        assert config.model == "my-model"
        assert config.provider is not None
        assert config.provider.adapter == "openai"
        assert config.provider.api_key == "sk-test"
        assert config.provider.base_url == "https://api.example.com/v1"

    def test_config_file(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "model": "file-model",
                    "cwd": "/tmp",
                    "provider": {
                        "adapter": "anthropic",
                        "api_key": "sk-file",
                        "base_url": "https://gw.example.com",
                    },
                }
            )
        )

        config = build_engine_config(config_file=str(cfg))
        assert config.model == "file-model"
        assert config.provider is not None
        assert config.provider.adapter == "anthropic"
        assert config.provider.api_key == "sk-file"

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("NONO_MODEL", "env-model")
        config = build_engine_config(model="cli-model")
        assert config.model == "cli-model"

    def test_no_hardcoded_secret(self):
        """Ensure the CLI path never carries a hardcoded key."""
        import inspect

        from nono_agent import adapters  # noqa: F401
        from nono_agent.adapters import cli as cli_module

        source = inspect.getsource(cli_module)
        assert "sk-cp-" not in source
