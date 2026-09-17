"""可选 API Key 鉴权。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def install_api_key_auth(app: FastAPI, api_key: str | None) -> None:
    """设置 api_key 后，除 /health 外均需 Bearer 或 X-API-Key。"""
    if api_key is None:
        return

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next: Any) -> Any:
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = (
            auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-api-key", "")
        )
        if token != api_key:
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
        return await call_next(request)
