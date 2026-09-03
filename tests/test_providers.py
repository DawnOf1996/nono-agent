"""Tests for LLM providers."""

import pytest

from nono_agent.adapters.testing import MockProvider
from nono_agent.providers.base import ProviderRegistry, create_default_registry


class TestProviderRegistry:
    def test_register_and_resolve(self):
        registry = ProviderRegistry()
        registry.register("test-", MockProvider)

        provider = registry.resolve("test-model-v1")
        assert provider.name == "mock"

    def test_resolve_by_prefix(self):
        registry = ProviderRegistry()
        registry.register("custom-", MockProvider)

        provider = registry.resolve("custom-model")
        assert provider is not None

    def test_resolve_not_found(self):
        registry = ProviderRegistry()

        with pytest.raises(ValueError, match="No provider registered"):
            registry.resolve("unknown-model")

    def test_register_instance(self):
        registry = ProviderRegistry()
        mock = MockProvider()
        registry.register_instance("mock-", mock)

        provider = registry.resolve("mock-test")
        assert provider is mock

    def test_cached_instance(self):
        registry = ProviderRegistry()
        registry.register("test-", MockProvider)

        p1 = registry.resolve("test-model-1")
        p2 = registry.resolve("test-model-2")
        assert p1 is p2  # Same cached instance

    def test_create_default_registry(self):
        registry = create_default_registry()
        assert set(registry._adapter_map) == {
            "openai_chat_completions",
            "openai_responses",
            "anthropic_messages",
        }
        assert "claude-" not in registry._providers
        assert "gpt-" not in registry._providers

    def test_no_prefix_fallback(self):
        registry = create_default_registry()
        with pytest.raises(ValueError, match="No provider registered"):
            registry.resolve("claude-sonnet-4-20250514")


class TestCanonicalAdapters:
    def test_three_protocols_registered(self):
        registry = create_default_registry()
        assert set(registry._adapter_map) == {
            "openai_chat_completions",
            "openai_responses",
            "anthropic_messages",
        }

    def test_chat_completions_uses_openai_sdk(self):
        from nono_agent.core.config import ProviderConfig
        from nono_agent.providers.openai_chat_completions import OpenAIChatCompletionsProvider

        registry = create_default_registry()
        provider = registry.resolve(
            "MiniMax-M3",
            ProviderConfig(
                model_id="MiniMax-M3",
                adapter="openai_chat_completions",
                base_url="https://api.minimaxi.com/v1",
            ),
        )
        assert isinstance(provider, OpenAIChatCompletionsProvider)
        assert provider.name == "openai_chat_completions"

    def test_anthropic_messages_uses_anthropic_sdk(self):
        from nono_agent.core.config import ProviderConfig
        from nono_agent.providers.anthropic_messages import AnthropicMessagesProvider

        registry = create_default_registry()
        official = registry.resolve(
            "claude-sonnet-4-20250514",
            ProviderConfig(
                model_id="claude-sonnet-4-20250514",
                adapter="anthropic_messages",
                api_key="sk",
            ),
        )
        gateway = registry.resolve(
            "my-model",
            ProviderConfig(
                model_id="my-model",
                adapter="anthropic_messages",
                base_url="https://api.minimaxi.com/anthropic",
            ),
        )
        assert isinstance(official, AnthropicMessagesProvider)
        assert isinstance(gateway, AnthropicMessagesProvider)
        assert official.name == "anthropic_messages"

    def test_openai_responses_uses_openai_sdk(self):
        from nono_agent.core.config import ProviderConfig
        from nono_agent.providers.openai_responses import OpenAIResponsesProvider

        registry = create_default_registry()
        official = registry.resolve(
            "gpt-4o",
            ProviderConfig(model_id="gpt-4o", adapter="openai_responses", api_key="sk"),
        )
        gateway = registry.resolve(
            "m",
            ProviderConfig(
                model_id="m",
                adapter="openai_responses",
                base_url="https://gw.example.com/v1",
            ),
        )
        assert isinstance(official, OpenAIResponsesProvider)
        assert isinstance(gateway, OpenAIResponsesProvider)
        assert official.name == "openai_responses"

    def test_unknown_adapter_lists_canonical_names(self):
        from nono_agent.core.config import ProviderConfig

        registry = create_default_registry()
        with pytest.raises(ValueError, match="openai_chat_completions"):
            registry.resolve(
                "m",
                ProviderConfig(model_id="m", adapter="not_a_protocol"),
            )

    def test_old_adapter_names_are_rejected(self):
        from nono_agent.core.config import ProviderConfig

        registry = create_default_registry()
        for name in (
            "openai",
            "anthropic",
            "messages_compatible",
            "responses",
            "responses_compatible",
        ):
            with pytest.raises(ValueError, match="Unknown adapter"):
                registry.resolve("m", ProviderConfig(model_id="m", adapter=name))


class TestMockProvider:
    @pytest.mark.asyncio
    async def test_stream_basic(self):
        provider = MockProvider(
            responses=[{"text": "Hello world", "tool_calls": []}]
        )

        events = []
        async for event in provider.stream(
            messages=[], system_prompt="", tools=[]
        ):
            events.append(event)

        # Should have stream events + final assistant message
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_stream_with_tool_calls(self):
        provider = MockProvider(
            responses=[{
                "text": "",
                "tool_calls": [
                    {"name": "search", "id": "tc_1", "input": {"query": "test"}},
                ],
            }]
        )

        events = []
        async for event in provider.stream(
            messages=[], system_prompt="", tools=[]
        ):
            events.append(event)

        # Check that tool_use content block was emitted
        from nono_agent.core.types import AssistantMessage
        assistant_msgs = [e for e in events if isinstance(e, AssistantMessage)]
        assert len(assistant_msgs) == 1

        content = assistant_msgs[0].message["content"]
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0]["name"] == "search"

    @pytest.mark.asyncio
    async def test_call_recording(self):
        provider = MockProvider()

        async for _ in provider.stream(
            messages=[], system_prompt="You are helpful", tools=[{"name": "test"}]
        ):
            pass

        assert len(provider.calls) == 1
        assert provider.calls[0]["system_prompt"] == "You are helpful"
        assert len(provider.calls[0]["tools"]) == 1

    def test_token_counting(self):
        provider = MockProvider()
        count = provider.count_tokens([], "test", [])
        assert isinstance(count, int)
        assert count >= 0
