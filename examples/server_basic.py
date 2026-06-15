"""Server Basic Example — FastAPI + SSE agent server.

Run with:
    python examples/server_basic.py

Then interact with the API:
    # Create a session
    curl -X POST http://localhost:8000/session/new

    # Send a message (SSE stream)
    curl -N -X POST http://localhost:8000/chat \
        -H "Content-Type: application/json" \
        -d '{"message": "Hello!", "session_id": "<session_id>"}'

    # List sessions
    curl http://localhost:8000/sessions

    # Health check
    curl http://localhost:8000/health
"""

from nono_agent import EngineConfig
from nono_agent.adapters.server import create_server_app, run_server


def main():
    """Start the agent server."""

    # Configure the default engine
    config = EngineConfig(
        model="claude-sonnet-4-20250514",
        cwd=".",
        include_partial_messages=True,  # Stream events to clients
    )

    # Create the FastAPI app
    app = create_server_app(
        config,
        cors_origins=["*"],  # Allow all origins for development
    )

    # For production, use a WSGI server:
    #   uvicorn examples.server_basic:app --host 0.0.0.0 --port 8000

    print("Starting Nono Agent Server...")
    print("  POST /chat          — Send a message (SSE stream)")
    print("  POST /session/new   — Create a new session")
    print("  GET  /sessions      — List active sessions")
    print("  GET  /health        — Health check")
    print()

    # Run with uvicorn
    run_server(
        config,
        host="0.0.0.0",
        port=8000,
        cors_origins=["*"],
    )


if __name__ == "__main__":
    main()
