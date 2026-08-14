"""CLI Basic Example — Minimal CLI agent usage.

Run with:
    python examples/cli_basic.py

Or using the CLI adapter directly:
    python -m nono_agent.adapters.cli chat --model claude-sonnet-4-20250514
"""

import asyncio

from nono_agent import EngineConfig, QueryEngine


async def main():
    """Simple example: send a message and print the response."""

    # Configure the engine
    config = EngineConfig(
        model="claude-sonnet-4-20250514",
        cwd=".",
        verbose=False,
    )

    # Use async context manager for automatic cleanup
    async with QueryEngine(config) as engine:
        print(f"Session: {engine.session_id}")
        print("-" * 50)

        # Submit a message and stream the response
        async for msg in engine.submit_message("What is the meaning of life?"):
            # msg is an SDKMessage — check its type
            if msg.type == "assistant":
                content = msg.message.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        print(f"Assistant: {block['text']}")

            elif msg.type == "result":
                print("\n--- Result ---")
                print(f"  Status: {'Error' if msg.is_error else 'Success'}")
                print(f"  Duration: {msg.duration_ms:.0f}ms")
                print(f"  Turns: {msg.num_turns}")
                u = msg.usage
                print(
                    f"  Tokens: in={u.input_tokens} out={u.output_tokens} "
                    f"cache_read={u.cache_read_input_tokens}"
                )


if __name__ == "__main__":
    asyncio.run(main())
