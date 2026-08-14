"""Custom Provider Example — How to implement and register your own LLM provider.

This example shows how to:
1. Implement the LLMProvider Protocol
2. Register it with the ProviderRegistry
3. Use it with QueryEngine
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from nono_agent import EngineConfig, ProviderRegistry, QueryEngine
from nono_agent.core.config import ThinkingConfig
from nono_agent.core.types import (
    AssistantMessage,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    Message,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    StreamEvent,
)


class MyCustomProvider:
    """A custom LLM provider that wraps a hypothetical API.

    This could be:
    - A local model (Ollama, vLLM, llama.cpp)
    - A custom API endpoint
    - A mock for testing
    """

    def __init__(self, config: Any = None) -> None:
        self.name = "custom"
        self.supports_thinking = False
        self.supports_tool_choice = True

        # Your API client setup here
        self._api_key = getattr(config, "api_key", "") if config else ""
        self._base_url = getattr(config, "base_url", "http://localhost:8080") if config else "http://localhost:8080"

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: ThinkingConfig | None = None,
        model: str = "",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent | Message, None]:
        """Stream a response from your custom API.

        Implement the streaming logic here. You need to:
        1. Convert internal messages to your API format
        2. Make the API call
        3. Yield StreamEvent objects as they arrive
        4. Yield a final AssistantMessage with complete content
        """
        # Step 1: Convert messages to your API format
        # (in this simulated example we don't send the messages anywhere)
        _ = self.normalize_messages(messages)

        # Step 2: Call your API (example using httpx)
        # In this example, we simulate a response
        response_text = f"Response from custom provider for model '{model}'"

        # Step 3: Yield stream events
        yield MessageStartEvent(
            message={"usage": {"input_tokens": 50, "output_tokens": 0}}
        )

        # Text content block
        yield ContentBlockStartEvent(
            index=0,
            content_block={"type": "text", "text": ""},
        )
        yield ContentBlockDeltaEvent(
            index=0,
            delta={"type": "text_delta", "text": response_text},
        )
        yield ContentBlockStopEvent(index=0)

        yield MessageDeltaEvent(
            delta={"stop_reason": "end_turn"},
            usage={"output_tokens": len(response_text.split())},
        )
        yield MessageStopEvent()

        # Step 4: Yield complete assistant message
        yield AssistantMessage(
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": response_text}],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": len(response_text.split()),
                },
                "stop_reason": "end_turn",
            }
        )

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """Count tokens using your provider's tokenizer.

        If you don't have one, return an estimate.
        """
        total = len(system_prompt) // 4
        for msg in messages:
            if hasattr(msg, "message"):
                content = msg.message.get("content", "")
                if isinstance(content, str):
                    total += len(content) // 4
        return total

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert internal messages to your API's format."""
        result = []
        for msg in messages:
            if hasattr(msg, "message"):
                result.append(msg.message)
        return result


async def main():
    """Example: Use a custom provider with QueryEngine."""

    # Step 1: Create a registry and register your provider
    registry = ProviderRegistry()
    registry.register("custom-", MyCustomProvider)

    # Step 2: Configure the engine to use your model prefix
    config = EngineConfig(
        model="custom-my-model-v1",  # Will match "custom-" prefix
        cwd=".",
    )

    # Step 3: Create engine with custom registry
    engine = QueryEngine(config, provider_registry=registry)

    async with engine:
        print(f"Using custom provider for model: {config.model}")
        print("-" * 50)

        async for msg in engine.submit_message("Tell me a joke"):
            if msg.type == "assistant":
                content = msg.message.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        print(f"Custom Model: {block['text']}")

            elif msg.type == "result":
                print(f"\nDone in {msg.duration_ms:.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
