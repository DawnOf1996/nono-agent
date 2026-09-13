"""冒烟：用 nono-agent 自己的 MCP 客户端连 stdio 计算器 + 带认证的 HTTP 姓名服务。"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
HTTP_SCRIPT = ROOT / "mcp_servers" / "username_http.py"
HOST = "127.0.0.1"
PORT = 8765


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((HOST, PORT)) == 0


async def _wait_port(timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"HTTP MCP did not listen on {HOST}:{PORT}")


async def main() -> None:
    from nono_agent.mcp import MCPClient, MCPServerConfig, start_mcp_runtime

    owned = False
    proc: subprocess.Popen[str] | None = None
    if not _port_open():
        proc = subprocess.Popen(
            [sys.executable, "-u", str(HTTP_SCRIPT)],
            cwd=str(ROOT),
        )
        owned = True
        await _wait_port()

    try:
        health = httpx.get(f"http://{HOST}:{PORT}/health", timeout=5.0)
        health.raise_for_status()

        denied = httpx.post(f"http://{HOST}:{PORT}/mcp", timeout=5.0)
        if denied.status_code != 401:
            raise SystemExit(f"expected 401 without auth, got {denied.status_code}")

        runtime = await start_mcp_runtime(ROOT)
        try:
            if not runtime.enabled:
                raise SystemExit("MCP runtime disabled by settings")
            names = {tool.name for tool in runtime.tools}
            calc = next(t for t in runtime.tools if t.name == "mcp__calculator__add")
            user = next(t for t in runtime.tools if t.name == "mcp__username__get_user_name")

            added = await calc.execute({"a": 2, "b": 3}, None)
            if added.is_error or added.content != "5.0":
                raise SystemExit(f"calculator add failed: {added.content}")

            named = await user.execute({}, None)
            if named.is_error or named.content != "Alice":
                raise SystemExit(f"get_user_name failed: {named.content}")

            bad = MCPServerConfig(
                name="username-bad",
                transport="http",
                url=f"http://{HOST}:{PORT}/mcp",
                headers={
                    "Authorization": "Bearer wrong-token",
                    "X-Demo-User": "Alice",
                    "X-Demo-Auth": "header-ok",
                },
            )
            try:
                async with MCPClient(bad, workspace=ROOT) as client:
                    await client.list_tools()
                raise SystemExit("HTTP client should fail with a bad bearer token")
            except Exception as exc:
                if "Failed to start MCP server" not in str(exc) and "401" not in str(exc):
                    # 握手失败即可，不要求特定文案
                    if "unauthorized" not in str(exc).lower():
                        print(f"bad-token rejected as: {exc}")

            print("ok: stdio calculator add(2,3)=5.0")
            print("ok: HTTP get_user_name=Alice (Bearer + X-Demo-* headers)")
            print("ok: HTTP rejects missing/wrong auth")
            print("tools:", ", ".join(sorted(names)))
        finally:
            await runtime.close()
    finally:
        if owned and proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
