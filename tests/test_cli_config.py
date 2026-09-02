"""Tests for CLI config externalization (env vars / config file)."""

import json
from typing import Any

import pytest

from nono_agent.adapters.cli import build_engine_config, format_token_summary
from nono_agent.core.types import Usage


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

    # ------------------------------------------------------------------
    # model_context（模型上下文窗口）配置链路
    # ------------------------------------------------------------------

    def test_env_model_context(self, monkeypatch):
        monkeypatch.setenv("NONO_MODEL", "my-model")
        monkeypatch.setenv("NONO_API_KEY", "sk-test")
        monkeypatch.setenv("NONO_ADAPTER", "openai")
        monkeypatch.setenv("NONO_MODEL_CONTEXT", "128000")

        config = build_engine_config()
        assert config.provider is not None
        assert config.provider.model_context == 128_000
        assert config.effective_model_context == 128_000

    def test_config_file_provider_model_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "nono_agent.adapters.cli.load_user_config", lambda: {}
        )  # 隔离真实 ~/.nono-agent/config.json
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "model": "file-model",
                    "provider": {
                        "adapter": "openai",
                        "api_key": "sk-file",
                        "model_context": 196_608,
                    },
                }
            )
        )

        config = build_engine_config(config_file=str(cfg))
        assert config.provider is not None
        assert config.provider.model_context == 196_608
        assert config.effective_model_context == 196_608

    def test_config_file_top_level_model_context(self, tmp_path, monkeypatch):
        """顶层 model_context：同时落到 provider 段与引擎级字段（值一致）。"""
        monkeypatch.setattr("nono_agent.adapters.cli.load_user_config", lambda: {})
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "model": "file-model",
                    "model_context": 32_000,
                    "provider": {"adapter": "openai", "api_key": "sk-file"},
                }
            )
        )

        config = build_engine_config(config_file=str(cfg))
        assert config.provider is not None
        assert config.provider.model_context == 32_000
        assert config.model_context == 32_000
        assert config.effective_model_context == 32_000

    def test_env_model_context_overrides_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NONO_MODEL_CONTEXT", "128000")
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "provider": {
                        "adapter": "openai",
                        "api_key": "sk-file",
                        "model_context": 4_096,
                    }
                }
            )
        )

        config = build_engine_config(config_file=str(cfg))
        assert config.provider.model_context == 128_000
        assert config.effective_model_context == 128_000

    def test_user_config_model_context_without_provider(self, monkeypatch):
        """provider 未配置（无 adapter/api_key）时，顶层 model_context 落到引擎级。"""
        monkeypatch.setattr(
            "nono_agent.adapters.cli.load_user_config",
            lambda: {"model": "u", "model_context": 8_192},
        )

        config = build_engine_config()
        assert config.provider is None
        assert config.model_context == 8_192
        assert config.effective_model_context == 8_192

    def test_model_context_unconfigured_falls_back_to_default(self, monkeypatch):
        """未配置任何 model_context 时回退 DEFAULT_CONTEXT_WINDOW（无前缀猜测）。"""
        monkeypatch.setattr("nono_agent.adapters.cli.load_user_config", lambda: {})
        monkeypatch.delenv("NONO_MODEL_CONTEXT", raising=False)

        from nono_agent.utils.tokens import DEFAULT_CONTEXT_WINDOW

        config = build_engine_config()
        assert config.model_context is None
        assert config.effective_model_context == DEFAULT_CONTEXT_WINDOW

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


class TestContextUsageTui:
    """CLI TUI 的上下文用量行渲染。"""

    @staticmethod
    def _make_console() -> Any:
        import io

        from rich.console import Console

        return Console(file=io.StringIO(), width=200, force_terminal=False)

    @pytest.mark.asyncio
    async def test_consume_stream_prints_context_usage_line(self):
        from nono_agent.adapters.cli import _consume_stream
        from nono_agent.adapters.testing import create_test_engine

        engine = create_test_engine(
            responses=[{"text": "hi", "tool_calls": []}]
        )
        console = self._make_console()

        async with engine:
            result = await _consume_stream(engine, "Hi", console)

        assert result is not None
        assert result.context_usage is not None
        output = console.file.getvalue()
        assert "Context:" in output
        assert "system" in output and "tools" in output and "messages" in output

    @pytest.mark.asyncio
    async def test_consume_stream_skips_line_without_snapshot(self):
        """result.context_usage 为 None（无 usage 数据）时不渲染上下文行。"""
        from nono_agent.adapters.cli import _consume_stream
        from nono_agent.core.types import SDKResultMessage

        class _FakeEngine:
            async def submit_message(self, text):
                yield SDKResultMessage(subtype="success", context_usage=None)

        console = self._make_console()
        result = await _consume_stream(_FakeEngine(), "Hi", console)  # type: ignore[arg-type]

        assert result is not None
        assert "Context:" not in console.file.getvalue()


class TestTokenSummary:
    def test_summary_includes_all_categories_and_total(self):
        """CLI token 摘要必须覆盖四类 token 与总计，避免与实际总用量对不上。"""
        usage = Usage(
            input_tokens=1204,
            output_tokens=86,
            cache_read_input_tokens=500,
            cache_creation_input_tokens=320,
        )
        text = format_token_summary(usage)
        assert "in=1204" in text
        assert "out=86" in text
        assert "cache_read=500" in text
        assert "cache_creation=320" in text
        assert "total 2110" in text  # 1204 + 86 + 500 + 320

    def test_summary_matches_total_tokens_property(self):
        usage = Usage(input_tokens=10, output_tokens=5, cache_creation_input_tokens=7)
        assert f"total {usage.total_tokens}" in format_token_summary(usage)
