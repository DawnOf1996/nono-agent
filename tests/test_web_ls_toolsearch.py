"""Tests for LS / WebFetch(SSRF) / ToolSearch tools."""

import pytest

from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
from nono_agent.tools.builtin import LSTool, ToolSearchTool, WebSearchTool
from nono_agent.tools.builtin.web_fetch import is_url_blocked
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.registry import ToolRegistry


class TestIsUrlBlocked:
    def test_block_private_ips(self):
        assert is_url_blocked("http://127.0.0.1/")
        assert is_url_blocked("http://10.0.0.1/")
        assert is_url_blocked("http://192.168.1.1/x")
        assert is_url_blocked("http://172.16.0.1/")
        assert is_url_blocked("http://169.254.169.254/latest/meta-data/")  # 云元数据

    def test_block_non_http(self):
        assert is_url_blocked("file:///etc/passwd")
        assert is_url_blocked("ftp://example.com/x")

    def test_allow_public_with_resolver(self):
        assert not is_url_blocked("https://example.com/", resolver=lambda h: ["93.184.216.34"])

    def test_block_private_resolver(self):
        assert is_url_blocked("https://example.com/", resolver=lambda h: ["10.0.0.5"])


class TestLSTool:
    @pytest.mark.asyncio
    async def test_lists_entries(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        ctx = ToolUseContext(cwd=str(tmp_path), sandbox=PathSandbox(tmp_path))
        result = await LSTool().execute({}, ctx)
        assert "a.txt" in result.content
        assert "sub/" in result.content

    @pytest.mark.asyncio
    async def test_respects_ignore(self, tmp_path):
        (tmp_path / "build").mkdir()
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / ".gitignore").write_text("build/\n")
        ctx = ToolUseContext(
            cwd=str(tmp_path),
            sandbox=PathSandbox(tmp_path),
            ignore=IgnoreMatcher.from_workspace(tmp_path),
        )
        result = await LSTool().execute({}, ctx)
        assert "build/" not in result.content
        assert "a.txt" in result.content


class TestToolSearchTool:
    @pytest.mark.asyncio
    async def test_search(self):
        registry = ToolRegistry()
        for tool in create_fake_tools():
            registry.register(tool)
        ctx = ToolUseContext(cwd="/tmp", tool_registry=registry)

        result = await ToolSearchTool().execute({"query": "read"}, ctx)
        assert "read_file" in result.content
        assert "write_file" not in result.content

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = ToolUseContext(cwd="/tmp")
        result = await ToolSearchTool().execute({"query": "x"}, ctx)
        assert "no tool registry" in result.content


def create_fake_tools():
    from nono_agent.adapters.testing import MockTool

    return [
        MockTool(name="read_file", response="r", concurrent_safe=True, write_side_effects=False),
        MockTool(name="write_file", response="w", concurrent_safe=False, write_side_effects=True),
    ]


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_with_injected_search(self):
        async def fake_search(query, max_results):
            return [
                {"title": "Result A", "url": "https://a.example", "snippet": "snippet a"},
            ]

        tool = WebSearchTool(search_fn=fake_search)
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute({"query": "x"}, ctx)
        assert not result.is_error
        assert "Result A" in result.content

    @pytest.mark.asyncio
    async def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("NONO_SEARCH_URL", raising=False)
        monkeypatch.delenv("NONO_SEARCH_API_KEY", raising=False)
        tool = WebSearchTool()
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute({"query": "x"}, ctx)
        assert result.is_error
        assert "not configured" in result.content
