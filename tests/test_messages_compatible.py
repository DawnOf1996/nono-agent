"""Tests for the MessagesCompatibleProvider (non-original Messages gateway)."""

from nono_agent.core.config import ProviderConfig
from nono_agent.core.types import (
    AssistantMessage,
    ContentBlockDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
)
from nono_agent.providers.base import create_default_registry
from nono_agent.providers.messages_compatible import MessagesCompatibleProvider


def _make_provider() -> MessagesCompatibleProvider:
    return MessagesCompatibleProvider(
        ProviderConfig(
            model_id="my-model",
            adapter="messages_compatible",
            api_key="sk-test",
            base_url="https://gw.example.com/anthropic",
        )
    )


class TestRegistry:
    def test_adapter_registered(self):
        registry = create_default_registry()
        assert "messages_compatible" in registry._adapter_map

    def test_resolve_creates_provider(self):
        registry = create_default_registry()
        provider = registry.resolve(
            "my-model",
            ProviderConfig(model_id="my-model", adapter="messages_compatible", base_url="https://gw"),
        )
        assert provider.name == "messages_compatible"


class TestMessagesCompatibleProvider:
    def test_endpoint_and_headers(self):
        p = _make_provider()
        assert p._endpoint() == "https://gw.example.com/anthropic/messages"
        headers = p._headers()
        assert headers["x-api-key"] == "sk-test"

    def test_normalize_messages(self):
        p = _make_provider()
        msgs = [
            AssistantMessage(
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "tool_use", "id": "t1", "name": "read", "input": '{"a":1}'},
                    ],
                }
            )
        ]
        normalized = p.normalize_messages(msgs)
        tool_use = normalized[0]["content"][1]
        assert tool_use["input"] == {"a": 1}

    def test_map_message_start(self):
        p = _make_provider()
        ev = p._map_event(
            {"type": "message_start", "message": {"usage": {"input_tokens": 42}}}
        )
        assert isinstance(ev, MessageStartEvent)
        assert ev.message["usage"]["input_tokens"] == 42

    def test_map_delta_and_stop(self):
        p = _make_provider()
        delta = p._map_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hi"},
            }
        )
        assert isinstance(delta, ContentBlockDeltaEvent)
        assert delta.delta["text"] == "hi"

        stop = p._map_event({"type": "message_stop"})
        assert isinstance(stop, MessageStopEvent)

    def test_map_unknown_returns_none(self):
        p = _make_provider()
        assert p._map_event({"type": "ping"}) is None

    def test_count_tokens(self):
        p = _make_provider()
        assert p.count_tokens([], "", []) >= 0
