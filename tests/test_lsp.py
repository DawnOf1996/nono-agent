"""Tests for the LSP client + tool (using a mock stdio language server)."""

import sys
import textwrap

import pytest

from nono_agent.tools.builtin.lsp import LSPClient, LSPTool
from nono_agent.tools.context import ToolUseContext

_MOCK_SERVER = textwrap.dedent(
    '''
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            result = {"capabilities": {"definitionProvider": True}}
        elif method == "textDocument/definition":
            result = [{
                "uri": "file:///x.py",
                "range": {
                    "start": {"line": 3, "character": 0},
                    "end": {"line": 3, "character": 5},
                },
            }]
        elif method == "textDocument/references":
            result = [{
                "uri": "file:///x.py",
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 1, "character": 4},
                },
            }]
        elif method == "textDocument/diagnostic":
            result = {
                "kind": "full",
                "items": [{
                    "range": {
                        "start": {"line": 2, "character": 0},
                        "end": {"line": 2, "character": 1},
                    },
                    "message": "unused var",
                }],
            }
        else:
            result = None
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0", "id": rid, "result": result,
        }) + "\\n")
        sys.stdout.flush()
    '''
)


@pytest.fixture
def mock_lsp_script(tmp_path):
    script = tmp_path / "mock_lsp.py"
    script.write_text(_MOCK_SERVER)
    return script


def _client_factory(script):
    return LSPClient(sys.executable, [str(script)], root="/tmp")


class TestLSPClient:
    @pytest.mark.asyncio
    async def test_definition(self, mock_lsp_script):
        client = _client_factory(mock_lsp_script)
        async with client:
            locs = await client.definition("x.py", 1, 2)
            assert len(locs) == 1
            assert locs[0]["uri"] == "file:///x.py"

    @pytest.mark.asyncio
    async def test_diagnostics(self, mock_lsp_script):
        client = _client_factory(mock_lsp_script)
        async with client:
            diags = await client.diagnostics("x.py")
            assert len(diags) == 1
            assert "unused var" in diags[0]["message"]


class TestLSPTool:
    @pytest.mark.asyncio
    async def test_definition_via_tool(self, mock_lsp_script):
        tool = LSPTool(client_factory=lambda: _client_factory(mock_lsp_script))
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute(
            {"action": "definition", "path": "x.py", "line": 1, "character": 2}, ctx
        )
        assert not result.is_error
        assert "file:///x.py" in result.content

    @pytest.mark.asyncio
    async def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("NONO_LSP_COMMAND", raising=False)
        tool = LSPTool()
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute({"action": "definition", "path": "x.py"}, ctx)
        assert result.is_error
        assert "not configured" in result.content
