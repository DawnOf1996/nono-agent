"""Tests for CLI config via YAML catalog."""

from __future__ import annotations

from typing import Any

import pytest

from nono_agent.core.types import Usage
from nono_agent.providers.catalog import dump_yaml
from nono_app_cli.config import build_engine_config
from nono_app_cli.render import format_token_summary


def _user_yaml(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "agent_default_model": {"provider": "a", "model": "MiniMax-M3"},
        "llm_provider": {
            "providers": {
                "a": {
                    "display_name": "My-Provider",
                    "api_key_env": "A_API_KEY",
                    "api": "openai_responses",
                    "base_url": "https://api.minimax.cn/v1",
                    "models": [
                        {
                            "id": "MiniMax-M3",
                            "name": "MiniMax-M31",
                            "context_window": 1_000_000,
                            "max_tokens": 128_000,
                        },
                        {"id": "other", "name": "Other", "context_window": 32_000},
                    ],
                }
            }
        },
    }
    data.update(overrides)
    return dump_yaml(data)


def _write_user_yaml(text: str) -> None:
    from nono_agent.providers import catalog as catalog_mod

    path = catalog_mod.USER_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestBuildEngineConfig:
    def test_missing_env_raises(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.delenv("A_API_KEY", raising=False)
        with pytest.raises(ValueError, match="No API key"):
            build_engine_config(cwd=str(tmp_path))

    def test_from_credentials_file(self, tmp_path, monkeypatch):
        from nono_agent.providers.catalog import save_credential

        _write_user_yaml(_user_yaml())
        save_credential("A_API_KEY", "sk-file")
        monkeypatch.delenv("A_API_KEY", raising=False)
        config = build_engine_config(cwd=str(tmp_path))
        assert config.provider is not None
        assert config.provider.api_key == "sk-file"

    def test_from_yaml(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        config = build_engine_config(cwd=str(tmp_path))
        assert config.model == "MiniMax-M3"
        assert config.provider is not None
        assert config.provider.adapter == "openai_responses"
        assert config.provider.api_key == "sk-test"
        assert config.provider.base_url == "https://api.minimax.cn/v1"
        assert config.provider.model_context == 1_000_000
        assert config.provider.max_tokens == 128_000
        assert config.effective_model_context == 1_000_000

    def test_overlay_yaml(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(
            dump_yaml({"agent_default_model": {"provider": "a", "model": "other"}}),
            encoding="utf-8",
        )
        config = build_engine_config(cwd=str(tmp_path), config_file=str(overlay))
        assert config.model == "other"
        assert config.provider is not None
        assert config.provider.model_context == 32_000

    def test_cli_model_and_provider(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        config = build_engine_config(
            cwd=str(tmp_path), provider="a", model="other"
        )
        assert config.model == "other"
        assert config.provider is not None
        assert config.provider.model_id == "other"

    def test_nono_model_env(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        monkeypatch.setenv("NONO_MODEL", "other")
        config = build_engine_config(cwd=str(tmp_path))
        assert config.model == "other"

    def test_cli_model_overrides_env(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        monkeypatch.setenv("NONO_MODEL", "other")
        config = build_engine_config(cwd=str(tmp_path), model="MiniMax-M3")
        assert config.model == "MiniMax-M3"

    def test_log_api_requests_flag(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        monkeypatch.delenv("NONO_LOG_API_REQUESTS", raising=False)
        off = build_engine_config(cwd=str(tmp_path))
        assert off.log_api_requests is False
        assert off.provider is not None
        assert off.provider.log_api_requests is False

        on = build_engine_config(cwd=str(tmp_path), log_api_requests=True)
        assert on.log_api_requests is True
        assert on.provider is not None
        assert on.provider.log_api_requests is True

    def test_log_api_requests_env(self, tmp_path, monkeypatch):
        _write_user_yaml(_user_yaml())
        monkeypatch.setenv("A_API_KEY", "sk-test")
        monkeypatch.setenv("NONO_LOG_API_REQUESTS", "true")
        config = build_engine_config(cwd=str(tmp_path))
        assert config.log_api_requests is True
        assert config.provider is not None
        assert config.provider.log_api_requests is True

    def test_unconfigured_context_falls_back(self, tmp_path, monkeypatch):
        data = {
            "agent_default_model": {"provider": "a", "model": "m"},
            "llm_provider": {
                "providers": {
                    "a": {
                        "display_name": "A",
                        "api_key_env": "A_API_KEY",
                        "api": "openai_chat_completions",
                        "models": [{"id": "m"}],
                    }
                }
            },
        }
        _write_user_yaml(dump_yaml(data))
        monkeypatch.setenv("A_API_KEY", "sk")
        from nono_agent.utils.tokens import DEFAULT_CONTEXT_WINDOW

        config = build_engine_config(cwd=str(tmp_path))
        assert config.provider is not None
        assert config.provider.model_context is None
        assert config.effective_model_context == DEFAULT_CONTEXT_WINDOW

    def test_no_hardcoded_secret(self):
        import inspect

        from nono_app_cli import config as cli_module

        source = inspect.getsource(cli_module)
        assert "sk-cp-" not in source
        assert "NONO_API_KEY" not in source
        assert "NONO_ADAPTER" not in source


class TestContextUsageTui:
    """CLI TUI 的上下文用量行渲染。"""

    @staticmethod
    def _make_console() -> Any:
        import io

        from rich.console import Console

        return Console(file=io.StringIO(), width=200, force_terminal=False)

    @pytest.mark.asyncio
    async def test_consume_stream_prints_context_usage_line(self):
        from nono_app_cli.render import consume_stream as _consume_stream
        from tests.helpers import create_test_engine

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
        from nono_agent.core.types import SDKResultMessage
        from nono_app_cli.render import consume_stream as _consume_stream

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


class TestMcpStartupFeedback:
    def test_prints_failed_servers(self):
        from types import SimpleNamespace

        from nono_agent.mcp.runtime import MCPServerStatus
        from nono_app_cli.interactive import (
            print_mcp_startup_feedback as _print_mcp_startup_feedback,
        )

        lines: list[str] = []

        class _Console:
            def print(self, message: str) -> None:
                lines.append(message)

        engine = SimpleNamespace(
            mcp_runtime=SimpleNamespace(
                enabled=True,
                statuses=[
                    MCPServerStatus(
                        name="username",
                        transport="http",
                        enabled=True,
                        running=False,
                        url="http://127.0.0.1:8765/mcp",
                        error="Client error '404 Not Found' for url 'http://127.0.0.1:8765/mcp'",
                    )
                ],
            )
        )
        _print_mcp_startup_feedback(_Console(), engine)
        text = "\n".join(lines)
        assert "MCP startup errors" in text
        assert "username" in text
        assert "404 Not Found" in text
