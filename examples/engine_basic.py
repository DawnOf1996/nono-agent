"""Engine library example — consume QueryEngine.submit_message() as a library.

Run with:
    uv run python examples/engine_basic.py

Product CLI (terminal) is separate:
    uv run nono chat
"""

import asyncio

from nono_agent import EngineConfig, QueryEngine


async def main():
    """Simple example: send a message and print the response."""

    config = EngineConfig(
        model="claude-sonnet-4-20250514",
        cwd=".",
        verbose=False,
    )

    async with QueryEngine(config) as engine:
        print(f"Session: {engine.session_id}")
        print("-" * 50)

        async for msg in engine.submit_message("What is the meaning of life?"):
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
