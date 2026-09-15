"""Tests for the fallback_model switching path in the loop."""

import pytest

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import SDKResultMessage
from nono_agent.providers.base import FallbackTriggeredError, ProviderRegistry
from tests.helpers import MockProvider


class _FallbackProvider(MockProvider):
    """首次以 primary 调用时抛出 fallback，之后以 fallback 成功。"""

    def __init__(self):
        super().__init__(responses=[{"text": "from fallback", "tool_calls": []}])
        self.seen_models: list[str] = []

    async def stream(self, **kwargs):
        model = kwargs.get("model", "")
        self.seen_models.append(model)
        if model == "primary":
            raise FallbackTriggeredError(
                original_model="primary", fallback_model="fallback", reason="overloaded"
            )
        async for event in super().stream(**kwargs):
            yield event


class TestFallbackModel:
    @pytest.mark.asyncio
    async def test_falls_back_to_fallback_model(self):
        provider = _FallbackProvider()
        registry = ProviderRegistry()
        registry.register_instance("primary", provider)

        engine = QueryEngine(
            EngineConfig(model="primary", fallback_model="fallback"),
            provider_registry=registry,
        )

        async with engine:
            result = None
            async for msg in engine.submit_message("hi"):
                if isinstance(msg, SDKResultMessage):
                    result = msg

        assert result is not None
        assert not result.is_error
        assert result.result == "from fallback"
        # 先 primary（触发 fallback），再 fallback 成功
        assert provider.seen_models == ["primary", "fallback"]
