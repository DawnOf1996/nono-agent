"""Tests for LS / WebFetch(SSRF) / ToolSearch tools."""

import pytest

from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
from nono_agent.tools.builtin import LSTool, ToolSearchTool
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.registry import ToolRegistry
from nono_agent.utils.net_guard import is_url_blocked


class TestIsUrlBlocked:
    def test_block_private_ips(self):
        assert is_url_blocked("http://127.0.0.1/")
        assert is_url_blocked("http://10.0.0.1/")
        assert is_url_blocked("http://192.168.1.1/x")
        assert is_url_blocked("http://172.16.0.1/")
        assert is_url_blocked("http://169.254.169.254/latest/meta-data/")  # 云元数据
        assert is_url_blocked("http://[::1]/")
        assert is_url_blocked("http://[::ffff:127.0.0.1]/")

    def test_block_non_http(self):
        assert is_url_blocked("file:///etc/passwd")
        assert is_url_blocked("ftp://example.com/x")

    def test_allow_public_with_resolver(self):
        assert not is_url_blocked("https://example.com/", resolver=lambda h: ["93.184.216.34"])

    def test_block_private_resolver(self):
        assert is_url_blocked("https://example.com/", resolver=lambda h: ["10.0.0.5"])

    def test_allow_proxy_fake_ip(self):
        # Clash / sing-box TUN fake-ip（198.18.0.0/15），不是 RFC1918
        assert not is_url_blocked(
            "https://en.wikipedia.org/wiki/Peak_(video_game)",
            resolver=lambda h: ["198.18.0.69"],
        )

    def test_allow_ipv4_mapped_public(self):
        assert not is_url_blocked(
            "https://example.com/",
            resolver=lambda h: ["::ffff:93.184.216.34"],
        )

    def test_skip_dns_allows_hostname(self):
        assert not is_url_blocked(
            "https://en.wikipedia.org/wiki/x",
            resolver=lambda h: ["10.0.0.5"],
            skip_dns=True,
        )
        assert is_url_blocked("http://127.0.0.1/", skip_dns=True)


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
    from tests.helpers import MockTool

    return [
        MockTool(name="read_file", response="r", concurrent_safe=True, write_side_effects=False),
        MockTool(name="write_file", response="w", concurrent_safe=False, write_side_effects=True),
    ]


class TestWebSearchRemoved:
    """web_search 已下线，搜索能力改由工具插件（Tavily）提供。"""

    def test_not_registered_as_builtin(self):
        from nono_agent.tools.builtin import create_builtin_tools

        names = {tool.name for tool in create_builtin_tools()}
        assert "web_fetch" in names  # 原语保留
        assert "web_search" not in names
        assert not hasattr(__import__("nono_agent.tools.builtin", fromlist=["x"]), "WebSearchTool")


class TestNetGuardIsShared:
    """SSRF 判定必须只有一份，且不再挂在某个工具下面。

    放在 web_fetch 里的时候，删一个工具就等于删掉安全边界。
    """

    def test_toolplugins_uses_the_same_guard(self):
        from nono_agent.toolplugins import http_json
        from nono_agent.utils import net_guard

        assert http_json.is_url_blocked is net_guard.is_url_blocked
        assert http_json.http_proxy_enabled is net_guard.http_proxy_enabled
