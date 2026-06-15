"""Tests for LLM providers."""

import pytest
from nono_agent.providers.base import ProviderRegistry, create_default_registry
from nono_agent.adapters.testing import MockProvider


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
        # Should have default prefixes registered
        assert "claude-" in registry._providers or "claude-" in registry._instances


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
