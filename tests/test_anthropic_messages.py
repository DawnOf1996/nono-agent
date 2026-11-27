"""Tests for Anthropic Messages helpers used by AnthropicMessagesProvider."""

from nono_agent.core.config import ProviderConfig
from nono_agent.core.types import AssistantMessage
from nono_agent.providers._anthropic_messages_utils import message_delta_usage, normalize_usage
from nono_agent.providers.anthropic_messages import AnthropicMessagesProvider
from nono_agent.providers.base import create_default_registry


class TestRegistry:
    def test_adapter_registered(self):
        registry = create_default_registry()
        assert "anthropic_messages" in registry._adapter_map
        assert "messages_compatible" not in registry._adapter_map

    def test_resolve_creates_sdk_provider(self):
        registry = create_default_registry()
        provider = registry.resolve(
            "my-model",
            ProviderConfig(
                model_id="my-model",
                adapter="anthropic_messages",
                base_url="https://gw",
            ),
        )
        assert isinstance(provider, AnthropicMessagesProvider)
        assert provider.name == "anthropic_messages"


class TestNormalizeUsage:
    def test_normalize_messages(self):
        p = AnthropicMessagesProvider(
            ProviderConfig(model_id="m", adapter="anthropic_messages")
        )
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

    def test_count_tokens(self):
        p = AnthropicMessagesProvider()
        assert p.count_tokens([], "", []) >= 0

    def test_message_delta_fills_input_when_start_is_zero(self):
        start = normalize_usage(
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        )
        usage = message_delta_usage(
            start,
            {
                "input_tokens": 1252,
                "output_tokens": 219,
                "cache_read_input_tokens": 114,
                "cache_creation_input_tokens": 0,
            },
        )
        assert usage["output_tokens"] == 219
        assert usage["input_tokens"] == 1252
        assert usage["cache_read_input_tokens"] == 114
        assert "cache_creation_input_tokens" not in usage

    def test_message_delta_keeps_nonzero_start_input(self):
        start = normalize_usage({"input_tokens": 42, "output_tokens": 1})
        usage = message_delta_usage(
            start, {"input_tokens": 42, "output_tokens": 15}
        )
        assert usage == {"output_tokens": 15}
