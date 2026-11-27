"""LSP 工具 —— 定义跳转 / 引用 / 诊断（对标 CC LSPTool，P1 可选）。

用 stdio JSON-RPC 与 Language Server 通信（初始化 + definition/references/diagnostic）。
服务器命令经环境变量配置：``NONO_LSP_COMMAND`` / ``NONO_LSP_ARGS``（逗号分隔）。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result
from nono_agent.tools.protocol import ToolBase
from nono_agent.utils.process import hidden_kwargs


class LSPClient:
    """stdio JSON-RPC LSP 客户端（最小子集）。"""

    def __init__(self, command: str, args: list[str], root: str) -> None:
        self._command = command
        self._args = args
        self._root = root
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **hidden_kwargs(),
        )
        self._reader = asyncio.create_task(self._read_loop())
        await self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": Path(self._root).resolve().as_uri(),
                "capabilities": {},
            },
        )

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    async def __aenter__(self) -> LSPClient:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("LSP client not started")
        self._next_id += 1
        rid = self._next_id
        message = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[rid] = future
        self._proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=30)
        except TimeoutError:
            raise TimeoutError(f"LSP request '{method}' timed out.") from None
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
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
                continue
            future = self._pending.get(message["id"])
            if future is not None and not future.done():
                future.set_result(message)

    def _uri(self, path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = Path(self._root) / p
        return p.resolve().as_uri()

    async def definition(self, path: str, line: int, character: int) -> list[dict[str, Any]]:
        result = await self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": self._uri(path)},
                "position": {"line": line, "character": character},
            },
        )
        return self._extract_locations(result)

    async def references(self, path: str, line: int, character: int) -> list[dict[str, Any]]:
        result = await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": self._uri(path)},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            },
        )
        return self._extract_locations(result)

    async def diagnostics(self, path: str) -> list[dict[str, Any]]:
        result = await self._request(
            "textDocument/diagnostic",
            {"textDocument": {"uri": self._uri(path)}},
        )
        items = result.get("result", {}).get("items", []) if isinstance(result, dict) else []
        return [i for i in items if isinstance(i, dict)]

    @staticmethod
    def _extract_locations(result: dict[str, Any]) -> list[dict[str, Any]]:
        """把 definition/references 结果归一化为 Location 列表。"""
        value = result.get("result")
        if value is None:
            return []
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []
        return [loc for loc in value if isinstance(loc, dict)]


class LSPTool(ToolBase):
    """LSP 语言服务器工具（定义/引用/诊断）。"""

    _name = "lsp"
    _description = (
        "Query a Language Server for symbol definition, references, or diagnostics. "
        "Requires NONO_LSP_COMMAND to point to a language server binary."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["definition", "references", "diagnostics"],
            },
            "path": {"type": "string", "description": "File path."},
            "line": {"type": "integer", "description": "0-based line number."},
            "character": {"type": "integer", "description": "0-based character offset."},
        },
        "required": ["action", "path"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = False

    def __init__(self, client_factory: Callable[[], Any] | None = None) -> None:
        self._client_factory = client_factory

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        action = input_params.get("action", "definition")
        path = input_params.get("path", "")
        if not path:
            return error_result(self._name, "No file path provided.")

        client = await self._get_client()
        if client is None:
            return error_result(
                self._name,
                "LSP is not configured. Set NONO_LSP_COMMAND to a language server binary.",
            )

        try:
            line = int(input_params.get("line", 0))
            character = int(input_params.get("character", 0))
            async with client:
                if action == "definition":
                    locations = await client.definition(path, line, character)
                elif action == "references":
                    locations = await client.references(path, line, character)
                elif action == "diagnostics":
                    diags = await client.diagnostics(path)
                    if not diags:
                        return ok_result(self._name, "(no diagnostics)")
                    lines = [
                        (
                            f"{d.get('range', {}).get('start', {}).get('line', '?')}:"
                            f"{d.get('message', '')}"
                        )
                        for d in diags
                    ]
                    return ok_result(self._name, "\n".join(lines))
                else:
                    return error_result(self._name, f"Unknown action '{action}'.")
        except Exception as exc:
            return error_result(self._name, f"LSP request failed: {exc}")

        if not locations:
            return ok_result(self._name, "(no results)")
        lines = [
            f"{loc.get('uri', '')}:{loc.get('range', {}).get('start', {}).get('line', '?')}"
            for loc in locations
        ]
        return ok_result(self._name, "\n".join(lines))

    async def _get_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        command = os.environ.get("NONO_LSP_COMMAND")
        if not command:
            return None
        args = [a for a in os.environ.get("NONO_LSP_ARGS", "").split(",") if a]
        return LSPClient(command, args, os.getcwd())
