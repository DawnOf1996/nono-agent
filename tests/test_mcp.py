"""Tests for the MCP client and tool bridge (using a mock stdio server)."""

import json
import sys
import textwrap

import pytest

from nono_agent.mcp import MCPClient, MCPServerConfig, bridge_mcp_server, load_mcp_config

_MOCK_SERVER = textwrap.dedent(
    """
    import json, sys

    TOOLS = [
        {"name": "echo", "description": "Echo input", "inputSchema": {"type": "object"}},
        {"name": "add", "description": "Add numbers", "inputSchema": {"type": "object"}},
    ]
    RESOURCES = [{"uri": "file:///x.txt", "name": "x.txt"}]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "mock"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = req["params"]["name"]
            result = {"content": [{"type": "text", "text": "ok:" + name}]}
        elif method == "resources/list":
            result = {"resources": RESOURCES}
        elif method == "resources/read":
            result = {"contents": [{"uri": req["params"]["uri"], "text": "DATA"}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.fixture
def mock_server_script(tmp_path):
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(_MOCK_SERVER)
    return script


def _config(mock_server_script) -> MCPServerConfig:
    return MCPServerConfig(
        name="mock",
        command=sys.executable,
        args=[str(mock_server_script)],
    )


class TestLoadMCPConfig:
    def test_load(self, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "filesystem": {
                            "command": "npx",
                            "args": ["-y", "fs"],
                            "transport": "stdio",
                        },
                        "remote": {"transport": "sse", "url": "https://x/mcp"},
                    }
                }
            )
        )
        servers = load_mcp_config(cfg)
        assert "filesystem" in servers
        assert servers["filesystem"].command == "npx"
        assert servers["remote"].transport == "sse"

    def test_missing(self, tmp_path):
        assert load_mcp_config(tmp_path / "nope.json") == {}


class TestMCPClient:
    @pytest.mark.asyncio
    async def test_lifecycle_and_tools(self, mock_server_script):
        client = MCPClient(_config(mock_server_script))
        async with client:
            tools = await client.list_tools()
            assert {t["name"] for t in tools} == {"echo", "add"}
            result = await client.call_tool("echo", {})
            assert result == "ok:echo"

    @pytest.mark.asyncio
    async def test_resources(self, mock_server_script):
        client = MCPClient(_config(mock_server_script))
        async with client:
            resources = await client.list_resources()
            assert resources[0]["uri"] == "file:///x.txt"
            text = await client.read_resource("file:///x.txt")
            assert text == "DATA"


class TestBridge:
    @pytest.mark.asyncio
    async def test_bridge_mcp_server(self, mock_server_script):
        tools, client = await bridge_mcp_server(_config(mock_server_script))
        try:
            names = {t.name for t in tools}
            assert "mcp__mock__echo" in names
            assert "mcp__mock__add" in names

            echo = next(t for t in tools if t.name == "mcp__mock__echo")
            result = await echo.execute({}, None)
            assert not result.is_error
            assert result.content == "ok:echo"
        finally:
            await client.close()
