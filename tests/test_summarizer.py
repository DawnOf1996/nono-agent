"""Tests for the ContextCollapse summarizer and config switch."""

import pytest

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.pipeline.summarizer import ProviderSummarizer
from nono_agent.providers.base import ProviderRegistry
from tests.helpers import MockProvider


class TestProviderSummarizer:
    @pytest.mark.asyncio
    async def test_summarizes(self):
        provider = MockProvider(responses=[{"text": "A concise summary.", "tool_calls": []}])
        summarizer = ProviderSummarizer(lambda: provider, model="test-model")
        summary = await summarizer(
            [{"role": "user", "content": "hello"}], model_id="test-model"
        )
        assert summary == "A concise summary."


class TestContextCollapseSwitch:
    def test_disabled_by_default(self):
        engine = QueryEngine(EngineConfig(model="test-model"))
        names = [s.name for s in engine.pipeline.stages]
        assert "context_collapse" not in names

    def test_enabled_registers_stage(self):
        provider = MockProvider()
        registry = ProviderRegistry()
        registry.register_instance("test-model", provider)
        engine = QueryEngine(
            EngineConfig(model="test-model", context_collapse_enabled=True),
            provider_registry=registry,
        )
        names = [s.name for s in engine.pipeline.stages]
        assert "context_collapse" in names
        # 顺序：microcompact < context_collapse < auto_compact
        assert (
            names.index("microcompact")
            < names.index("context_collapse")
            < names.index("auto_compact")
        )
