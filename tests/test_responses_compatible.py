"""Tests for the ResponsesCompatibleProvider."""

from nono_agent.core.config import ProviderConfig
from nono_agent.core.types import UserMessage
from nono_agent.providers.base import create_default_registry
from nono_agent.providers.responses_compatible import ResponsesCompatibleProvider


class TestResponsesCompatible:
    def test_registered(self):
        registry = create_default_registry()
        assert "responses_compatible" in registry._adapter_map

    def test_endpoint(self):
        p = ResponsesCompatibleProvider(
            ProviderConfig(
                model_id="m", adapter="responses_compatible", base_url="https://gw/v1"
            )
        )
        assert p._endpoint() == "https://gw/v1/responses"

    def test_normalize(self):
        p = ResponsesCompatibleProvider()
        msgs = [UserMessage(message={"role": "user", "content": [{"type": "text", "text": "hi"}]})]
        items = p.normalize_messages(msgs)
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "user"

    def test_count_tokens(self):
        p = ResponsesCompatibleProvider()
        assert p.count_tokens([], "", []) >= 0
