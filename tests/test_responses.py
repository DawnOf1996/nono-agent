"""Tests for the Responses provider and shared utils."""

from nono_agent.core.config import ProviderConfig
from nono_agent.core.types import AssistantMessage, UserMessage
from nono_agent.providers._responses_utils import (
    normalize_responses_input,
    to_responses_tool,
)
from nono_agent.providers.base import create_default_registry
from nono_agent.providers.responses_provider import ResponsesProvider


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


class TestResponsesProvider:
    def test_registered(self):
        registry = create_default_registry()
        assert "responses" in registry._adapter_map

    def test_normalize_and_count(self):
        p = ResponsesProvider(
            ProviderConfig(model_id="gpt-4o", adapter="responses", api_key="sk")
        )
        msgs = [UserMessage(message={"role": "user", "content": [{"type": "text", "text": "hi"}]})]
        items = p.normalize_messages(msgs)
        assert items[0]["type"] == "message"
        assert p.count_tokens([], "", []) >= 0
