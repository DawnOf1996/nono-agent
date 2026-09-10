"""Tests for pre-HTTP LLM request dump (log_api_requests)."""

from __future__ import annotations

import json
import logging

import pytest

from nono_agent.core.config import EngineConfig, ProviderConfig
from nono_agent.core.types import UserMessage
from nono_agent.providers._request_log import (
    LOGGER_NAME,
    maybe_log_api_request,
    redact,
)
from nono_agent.providers.openai_chat_completions import OpenAIChatCompletionsProvider


class TestRedact:
    def test_redacts_sensitive_keys_recursively(self):
        raw = {
            "model": "m",
            "api_key": "sk-secret",
            "extra_headers": {"Authorization": "Bearer abc", "X-Custom": "ok"},
            "nested": [{"token": "t", "text": "hi"}],
        }
        out = redact(raw)
        assert out["model"] == "m"
        assert out["api_key"] == "***"
        assert out["extra_headers"]["Authorization"] == "***"
        assert out["extra_headers"]["X-Custom"] == "ok"
        assert out["nested"][0]["token"] == "***"
        assert out["nested"][0]["text"] == "hi"

    def test_leaves_plain_values_alone(self):
        assert redact("hello") == "hello"
        assert redact([1, 2]) == [1, 2]


class TestMaybeLog:
    def test_disabled_does_not_log(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        maybe_log_api_request(
            ProviderConfig(model_id="m", log_api_requests=False),
            protocol="openai_chat_completions",
            params={"model": "m", "messages": []},
        )
        assert "llm_http_request" not in caplog.text

    def test_enabled_logs_json_payload(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        maybe_log_api_request(
            ProviderConfig(model_id="m", log_api_requests=True),
            protocol="openai_chat_completions",
            params={"model": "m", "api_key": "sk-leak", "messages": [{"role": "user"}]},
        )
        assert "llm_http_request" in caplog.text
        line = next(
            rec.getMessage()
            for rec in caplog.records
            if "llm_http_request" in rec.getMessage()
        )
        payload = json.loads(line.split("llm_http_request ", 1)[1])
        assert payload["protocol"] == "openai_chat_completions"
        assert payload["params"]["model"] == "m"
        assert payload["params"]["api_key"] == "***"
        assert payload["params"]["messages"][0]["role"] == "user"

    def test_enabled_preserves_unicode(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        maybe_log_api_request(
            ProviderConfig(model_id="m", log_api_requests=True),
            protocol="openai_chat_completions",
            params={"messages": [{"role": "user", "content": "你好👋"}]},
        )
        line = next(
            rec.getMessage()
            for rec in caplog.records
            if "llm_http_request" in rec.getMessage()
        )
        payload = json.loads(line.split("llm_http_request ", 1)[1])
        assert payload["params"]["messages"][0]["content"] == "你好👋"

    def test_none_config_is_noop(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        maybe_log_api_request(None, protocol="x", params={"a": 1})
        assert "llm_http_request" not in caplog.text


class TestEngineConfigCopy:
    def test_copies_engine_flag_onto_provider(self):
        pc = ProviderConfig(model_id="m", adapter="openai_chat_completions")
        cfg = EngineConfig(model="m", provider=pc, log_api_requests=True)
        resolved = cfg.provider_config_for_api()
        assert resolved is not None
        assert resolved.log_api_requests is True
        assert pc.log_api_requests is False  # 原对象不变

    def test_off_leaves_provider_untouched(self):
        pc = ProviderConfig(model_id="m")
        cfg = EngineConfig(model="m", provider=pc, log_api_requests=False)
        assert cfg.provider_config_for_api() is pc


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeCompletions:
    def __init__(self) -> None:
        self.last_params: dict | None = None

    async def create(self, **params):
        self.last_params = params
        return _EmptyStream()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


class TestChatCompletionsHook:
    @pytest.mark.asyncio
    async def test_logs_assembled_params_before_http(self, monkeypatch):
        logged: list[dict] = []
        monkeypatch.setattr(
            "nono_agent.providers.openai_chat_completions.maybe_log_api_request",
            lambda config, *, protocol, params: logged.append(
                {"protocol": protocol, "params": params}
            ),
        )
        provider = OpenAIChatCompletionsProvider(
            ProviderConfig(
                model_id="MiniMax-M3",
                adapter="openai_chat_completions",
                log_api_requests=True,
                extra_body={"reasoning_split": True},
            )
        )
        client = _FakeClient()
        provider._client = client

        messages = [
            UserMessage(message={"role": "user", "content": "hi"}),
        ]
        async for _ in provider.stream(
            messages=messages,
            system_prompt="you are a test",
            tools=[{"name": "bash", "description": "run", "input_schema": {"type": "object"}}],
            model="MiniMax-M3",
        ):
            pass

        assert len(logged) == 1
        dump = logged[0]
        assert dump["protocol"] == "openai_chat_completions"
        params = dump["params"]
        assert params["model"] == "MiniMax-M3"
        assert params["messages"][0] == {"role": "system", "content": "you are a test"}
        assert params["messages"][1]["role"] == "user"
        assert params["stream"] is True
        assert params["extra_body"] == {"reasoning_split": True}
        assert params["tools"]

    @pytest.mark.asyncio
    async def test_off_does_not_emit_log(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider = OpenAIChatCompletionsProvider(
            ProviderConfig(model_id="m", adapter="openai_chat_completions")
        )
        provider._client = _FakeClient()
        async for _ in provider.stream(
            messages=[], system_prompt="", tools=[], model="m"
        ):
            pass
        assert "llm_http_request" not in caplog.text
