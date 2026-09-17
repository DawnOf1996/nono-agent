"""MCP 客户端 —— 基于官方 SDK 2.x，覆盖 stdio / Streamable HTTP / SSE。"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from nono_agent.mcp.config import MCPServerConfig
from nono_agent.mcp.launcher import LaunchRecipe, MCPLaunchError, resolve_launch


class MCPClientError(RuntimeError):
    """MCP 客户端错误。"""


def describe_exception(exc: BaseException) -> str:
    """展开 TaskGroup / ExceptionGroup，露出真正的连接或 HTTP 错误。"""
    parts: list[str] = []
    seen: set[int] = set()

    def walk(err: BaseException) -> None:
        if id(err) in seen:
            return
        seen.add(id(err))
        if isinstance(err, BaseExceptionGroup):
            for inner in err.exceptions:
                walk(inner)
            return
        text = str(err).strip()
        if not text or text.startswith("unhandled errors in a TaskGroup"):
            if err.__cause__ is not None:
                walk(err.__cause__)
            elif err.__context__ is not None:
                walk(err.__context__)
            else:
                name = type(err).__name__
                if name not in {"ExceptionGroup", "BaseExceptionGroup"}:
                    parts.append(name)
            return
        parts.append(text)
        # 外层已经写过原因（如 MCPClientError）就不要再拆 __cause__，
        # 否则会出现 "Not Found; Not Found"。
        if isinstance(err, MCPClientError):
            return
        cause = err.__cause__
        if cause is not None:
            nested = str(cause).strip()
            if nested and nested not in text:
                walk(cause)

    walk(exc)
    return _join_unique_parts(parts) or type(exc).__name__


def _join_unique_parts(parts: list[str]) -> str:
    unique = list(dict.fromkeys(part for part in parts if part))
    kept: list[str] = []
    for item in unique:
        if any(item != other and item in other for other in unique):
            continue
        kept.append(item)
    return "; ".join(kept)


class MCPClient:
    """官方 SDK Client 的生命周期包装。"""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        recipe: LaunchRecipe | None = None,
        workspace: str | Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._recipe = recipe
        self._workspace = Path(workspace) if workspace is not None else None
        self._data_dir = data_dir
        self._sdk: Client | None = None
        self._stack: AsyncExitStack | None = None

    @property
    def config(self) -> MCPServerConfig:
        return self._config

    @property
    def recipe(self) -> LaunchRecipe | None:
        return self._recipe

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """连接服务器并完成协议握手。"""
        if self._sdk is not None:
            return
        try:
            recipe = self._recipe or resolve_launch(
                self._config,
                self._workspace or Path.cwd(),
                data_dir=self._data_dir,
            )
        except MCPLaunchError as exc:
            raise MCPClientError(str(exc)) from exc
        self._recipe = recipe

        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            sdk = await self._open_sdk(stack, recipe)
            self._sdk = await stack.enter_async_context(sdk)
            self._stack = stack
        except Exception as exc:
            await stack.aclose()
            raise MCPClientError(
                f"Failed to start MCP server {self._config.name!r}: "
                f"{describe_exception(exc)}"
            ) from exc

    async def close(self) -> None:
        """关闭连接并回收本地进程。"""
        stack = self._stack
        self._stack = None
        self._sdk = None
        if stack is not None:
            await stack.aclose()

    async def __aenter__(self) -> MCPClient:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _open_sdk(self, stack: AsyncExitStack, recipe: LaunchRecipe) -> Client:
        if recipe.transport == "stdio":
            if not recipe.argv:
                raise MCPClientError("stdio launch recipe is missing argv.")
            errlog = sys.stderr
            if recipe.stderr_log is not None:
                recipe.stderr_log.parent.mkdir(parents=True, exist_ok=True)
                errlog = stack.enter_context(
                    recipe.stderr_log.open("a", encoding="utf-8", errors="replace")
                )
            params = StdioServerParameters(
                command=recipe.argv[0],
                args=recipe.argv[1:],
                env=recipe.env or None,
                cwd=recipe.cwd,
            )
            return Client(stdio_client(params, errlog=errlog))

        if recipe.transport == "http":
            if not recipe.url:
                raise MCPClientError("http launch recipe is missing url.")
            import httpx2

            http = httpx2.AsyncClient(
                headers=recipe.headers or None,
                follow_redirects=True,
                timeout=httpx2.Timeout(30.0, read=300.0),
            )
            await stack.enter_async_context(http)
            return Client(streamable_http_client(recipe.url, http_client=http))

        if recipe.transport == "sse":
            if not recipe.url:
                raise MCPClientError("sse launch recipe is missing url.")
            return Client(sse_client(recipe.url, headers=recipe.headers or None))

        raise MCPClientError(f"Unsupported MCP transport {recipe.transport!r}.")

    # ------------------------------------------------------------------
    # 高层 API
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._require_sdk().list_tools()
        tools: list[dict[str, Any]] = []
        for tool in result.tools:
            tools.append(_tool_to_dict(tool))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await self._require_sdk().call_tool(name, arguments)
        except MCPError as exc:
            raise MCPClientError(f"MCP tool '{name}' error: {exc}") from exc
        text = _content_to_text(result.content)
        if result.is_error:
            raise MCPClientError(f"MCP tool '{name}' error: {text or 'unknown error'}")
        return text

    async def list_resources(self) -> list[dict[str, Any]]:
        result = await self._require_sdk().list_resources()
        resources: list[dict[str, Any]] = []
        for item in result.resources:
            resources.append(_model_to_dict(item))
        return resources

    async def read_resource(self, uri: str) -> str:
        try:
            result = await self._require_sdk().read_resource(uri)
        except MCPError as exc:
            raise MCPClientError(f"MCP read resource error: {exc}") from exc
        return _resource_to_text(result.contents)

    def _require_sdk(self) -> Client:
        if self._sdk is None:
            raise MCPClientError("MCP client not started.")
        return self._sdk


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    data = _model_to_dict(tool)
    schema = (
        data.get("inputSchema")
        or data.get("input_schema")
        or {"type": "object", "properties": {}}
    )
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    return {
        "name": str(data.get("name") or getattr(tool, "name", "unknown")),
        "description": str(data.get("description") or getattr(tool, "description", "") or ""),
        "inputSchema": schema,
    }


def _model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True, exclude_none=True, mode="json")
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def _content_to_text(content: Any) -> str:
    parts: list[str] = []
    for item in content or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
            continue
        if isinstance(item, dict):
            raw = item.get("text")
            if isinstance(raw, str) and raw:
                parts.append(raw)
    return "\n".join(parts)


def _resource_to_text(contents: Any) -> str:
    parts: list[str] = []
    for item in contents or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
            continue
        if isinstance(item, dict):
            raw = item.get("text")
            if isinstance(raw, str) and raw:
                parts.append(raw)
    return "\n".join(parts)
