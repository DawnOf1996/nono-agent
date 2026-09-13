"""Streamable HTTP 姓名 MCP —— 校验 Bearer token 与自定义请求头。

nono-agent 只负责连接 URL；本进程需单独启动：

    uv run python runtime-test/mcp_servers/username_http.py
"""

from __future__ import annotations

import os
from contextvars import ContextVar

from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse, PlainTextResponse

EXPECTED_TOKEN = os.environ.get("DEMO_MCP_TOKEN", "runtime-test-secret")
EXPECTED_HEADER_AUTH = os.environ.get("DEMO_MCP_HEADER_AUTH", "header-ok")
PORT = int(os.environ.get("DEMO_MCP_PORT", "8889"))
HOST = os.environ.get("DEMO_MCP_HOST", "127.0.0.1")

_current_user: ContextVar[str] = ContextVar("current_user", default="")


class DemoAuthMiddleware:
    """纯 ASGI 中间件，避免 BaseHTTPMiddleware 打断 Streamable HTTP。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await JSONResponse({"ok": True, "server": "username"})(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        user = headers.get("x-demo-user", "").strip()
        extra = headers.get("x-demo-auth", "")

        if auth != f"Bearer {EXPECTED_TOKEN}":
            await PlainTextResponse(
                "unauthorized: bad bearer token",
                status_code=401,
            )(scope, receive, send)
            return
        if extra != EXPECTED_HEADER_AUTH:
            await PlainTextResponse(
                "unauthorized: missing or bad X-Demo-Auth",
                status_code=401,
            )(scope, receive, send)
            return
        if not user:
            await PlainTextResponse(
                "unauthorized: missing X-Demo-User",
                status_code=401,
            )(scope, receive, send)
            return

        reset = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(reset)


server = MCPServer("username")


@server.tool()
def get_user_name() -> str:
    """Return the display name from the authenticated X-Demo-User request header."""
    name = _current_user.get()
    return name or "unknown"


app = DemoAuthMiddleware(server.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    print(f"username MCP listening on http://{HOST}:{PORT}/mcp", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
