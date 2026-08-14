"""MCP 客户端 —— stdio JSON-RPC 传输与生命周期。

实现 Model Context Protocol 的最小子集：
- ``initialize`` 握手
- ``tools/list`` / ``tools/call``（工具桥接）
- ``resources/list`` / ``resources/read``（资源）
- 进程生命周期管理与健康检查
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nono_agent.mcp.config import MCPServerConfig


class MCPClientError(RuntimeError):
    """MCP 客户端错误。"""


class MCPClient:
    """stdio JSON-RPC MCP 客户端。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._initialized = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动服务器进程并完成初始化握手。"""
        if self._proc is not None:
            return
        if self._config.transport != "stdio":
            raise MCPClientError(
                f"Only stdio transport is implemented (got {self._config.transport!r})."
            )
        if not self._config.command:
            raise MCPClientError("MCP server config requires a 'command'.")

        env = dict(self._config.env)
        # 静态令牌经环境变量传给 stdio 服务器
        if self._config.token:
            env.setdefault("MCP_AUTH_TOKEN", self._config.token)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env or None,
            )
        except OSError as exc:
            raise MCPClientError(f"Failed to start MCP server: {exc}") from exc

        self._reader_task = asyncio.create_task(self._read_loop())

        init_result = await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nono-agent", "version": "0.1.0"},
            },
        )
        if "result" not in init_result:
            raise MCPClientError(f"MCP initialize failed: {init_result}")

        self._initialized = True

    async def close(self) -> None:
        """关闭客户端并终止服务器进程。"""
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
        self._initialized = False

    async def __aenter__(self) -> MCPClient:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # JSON-RPC
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送请求并等待响应。"""
        if self._proc is None or self._proc.stdin is None:
            raise MCPClientError("MCP client not started.")

        self._next_id += 1
        req_id = self._next_id
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        self._proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

        try:
            return await asyncio.wait_for(future, timeout=60)
        except TimeoutError:
            raise MCPClientError(f"MCP request '{method}' timed out.") from None
        finally:
            self._pending.pop(req_id, None)

    async def _read_loop(self) -> None:
        """读取 stdout 的 NDJSON 响应并解析到 pending futures。"""
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            if "id" not in message:
                continue  # 通知，忽略
            future = self._pending.get(message["id"])
            if future is not None and not future.done():
                future.set_result(message)

    # ------------------------------------------------------------------
    # 高层 API
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """列出服务器提供的工具。"""
        result = await self._request("tools/list", {})
        tools = result.get("result", {}).get("tools", [])
        return [t for t in tools if isinstance(t, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用工具并返回文本结果。"""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        if "error" in result:
            raise MCPClientError(f"MCP tool '{name}' error: {result['error']}")
        content = result.get("result", {}).get("content", [])
        return self._extract_text(content)

    async def list_resources(self) -> list[dict[str, Any]]:
        """列出资源。"""
        result = await self._request("resources/list", {})
        resources = result.get("result", {}).get("resources", [])
        return [r for r in resources if isinstance(r, dict)]

    async def read_resource(self, uri: str) -> str:
        """读取资源内容。"""
        result = await self._request("resources/read", {"uri": uri})
        if "error" in result:
            raise MCPClientError(f"MCP read resource error: {result['error']}")
        contents = result.get("result", {}).get("contents", [])
        return self._extract_text(contents)

    @staticmethod
    def _extract_text(content: list[dict[str, Any]]) -> str:
        """从 MCP content 列表提取文本。"""
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
