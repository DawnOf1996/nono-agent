"""Tests for the Responses provider and shared utils."""

import pytest

from nono_agent.core.config import ProviderConfig
from nono_agent.core.types import AssistantMessage, UserMessage
from nono_agent.providers._openai_responses_utils import (
    normalize_responses_input,
    to_responses_tool,
)
from nono_agent.providers.base import create_default_registry
from nono_agent.providers.openai_responses import OpenAIResponsesProvider


class TestNormalizeResponsesInput:
    def test_text_and_tool_use(self):
        msgs = [
            UserMessage(message={"role": "user", "content": [{"type": "text", "text": "hi"}]}),
            AssistantMessage(
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "thinking"},
                        {"type": "tool_use", "id": "t1", "name": "read", "input": {"a": 1}},
                    ],
                }
            ),
            UserMessage(
                message={
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "result"}],
                }
            ),
        ]
        items = normalize_responses_input(msgs)

        assert items[0] == {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hi"}],
        }
        # assistant text
        assert items[1]["type"] == "message"
        assert items[1]["role"] == "assistant"
        # function_call
        assert items[2]["type"] == "function_call"
        assert items[2]["name"] == "read"
        assert items[2]["arguments"] == '{"a": 1}'
        # function_call_output
        assert items[3]["type"] == "function_call_output"
        assert items[3]["call_id"] == "t1"
        assert items[3]["output"] == "result"


class TestResponsesTool:
    def test_to_responses_tool(self):
        t = to_responses_tool(
            {"name": "read", "description": "d", "input_schema": {"type": "object"}}
        )
        assert t == {
            "type": "function",
            "name": "read",
            "description": "d",
            "parameters": {"type": "object"},
        }


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeResponses:
    def __init__(self) -> None:
        self.last_params: dict | None = None
        self.create_calls = 0

    async def create(self, **params):
        self.create_calls += 1
        self.last_params = params
        return _EmptyStream()


class _FakeResponsesClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


class TestOpenAIResponsesProvider:
    @pytest.mark.asyncio
    async def test_awaits_create_before_iterating(self):
        """stream=True 时 create() 返回协程，必须 await 后再 async for。"""
        provider = OpenAIResponsesProvider(
            ProviderConfig(model_id="gpt-4o", adapter="openai_responses", api_key="sk")
        )
        client = _FakeResponsesClient()
        provider._client = client

        events = [
            event
            async for event in provider.stream(
                messages=[],
                system_prompt="",
                tools=[],
                model="gpt-4o",
            )
        ]

        assert client.responses.create_calls == 1
        assert client.responses.last_params is not None
        assert client.responses.last_params["stream"] is True
        assert events

    def test_registered(self):
        registry = create_default_registry()
        assert "openai_responses" in registry._adapter_map
        assert "responses" not in registry._adapter_map
        assert "responses_compatible" not in registry._adapter_map

    def test_normalize_and_count(self):
        p = OpenAIResponsesProvider(
            ProviderConfig(model_id="gpt-4o", adapter="openai_responses", api_key="sk")
        )
        msgs = [UserMessage(message={"role": "user", "content": [{"type": "text", "text": "hi"}]})]
        items = p.normalize_messages(msgs)
        assert items[0]["type"] == "message"
        assert p.count_tokens([], "", []) >= 0
