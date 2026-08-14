"""Tests for the json_schema structured-output loop."""

import pytest

from nono_agent.adapters.testing import MockProvider
from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import SDKResultMessage
from nono_agent.providers.base import ProviderRegistry

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
}


class _RecordingProvider(MockProvider):
    """记录 stream 收到的 json_schema kwarg。"""

    def __init__(self, response_text: str):
        super().__init__(responses=[{"text": response_text, "tool_calls": []}])
        self.received_json_schema = None

    async def stream(self, **kwargs):
        self.received_json_schema = kwargs.get("json_schema")
        async for event in super().stream(**kwargs):
            yield event


def _engine(provider) -> QueryEngine:
    registry = ProviderRegistry()
    registry.register_instance("test-model", provider)
    return QueryEngine(
        EngineConfig(model="test-model", json_schema=_SCHEMA),
        provider_registry=registry,
    )


class TestStructuredOutput:
    @pytest.mark.asyncio
    async def test_parses_json_result(self):
        provider = _RecordingProvider('{"answer": 42}')
        engine = _engine(provider)
        async with engine:
            result = None
            async for msg in engine.submit_message("what is 6*7?"):
                if isinstance(msg, SDKResultMessage):
                    result = msg
            assert result is not None
            assert result.structured_output == {"answer": 42}
            # json_schema 已传给 provider
            assert provider.received_json_schema == _SCHEMA

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        provider = _RecordingProvider("not json at all")
        engine = _engine(provider)
        async with engine:
            result = None
            async for msg in engine.submit_message("hi"):
                if isinstance(msg, SDKResultMessage):
                    result = msg
            assert result is not None
            assert result.structured_output is None

    @pytest.mark.asyncio
    async def test_no_schema_no_structured_output(self):
        provider = MockProvider(responses=[{"text": '{"a": 1}', "tool_calls": []}])
        registry = ProviderRegistry()
        registry.register_instance("test-model", provider)
        engine = QueryEngine(EngineConfig(model="test-model"), provider_registry=registry)
        async with engine:
            result = None
            async for msg in engine.submit_message("hi"):
                if isinstance(msg, SDKResultMessage):
                    result = msg
            assert result is not None
            assert result.structured_output is None
