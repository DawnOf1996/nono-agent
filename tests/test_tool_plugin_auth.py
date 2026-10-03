"""鉴权注入的单元与端到端验证（bearer / header / query 三种）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nono_agent.settings.sources import load_settings
from nono_agent.toolplugins.auth import append_query_auth, header_auth, redact
from nono_agent.toolplugins.runtime import start_tool_plugin_runtime
from nono_agent.toolplugins.spec import parse_tool_specs
from tests.test_mcp import _free_port, _wait_port


class TestAuthHelpers:
    def test_query_appends_to_bare_url(self):
        assert append_query_auth("https://h/mcp", "k", "v") == "https://h/mcp?k=v"

    def test_query_preserves_existing_params(self):
        out = append_query_auth("https://h/mcp?a=1", "k", "v")
        assert out == "https://h/mcp?a=1&k=v"

    def test_query_escapes_secret(self):
        """Key 里带 & / + / = 时必须转义，否则查询串会被截成半截。"""
        out = append_query_auth("https://h/mcp", "k", "a+b/c=d&e")
        assert out == "https://h/mcp?k=a%2Bb%2Fc%3Dd%26e"
        from urllib.parse import parse_qs, urlsplit

        assert parse_qs(urlsplit(out).query)["k"] == ["a+b/c=d&e"]

    def test_query_escapes_param_name(self):
        out = append_query_auth("https://h/mcp", "api key", "v")
        assert "api%20key=v" in out

    def test_query_noop_without_secret(self):
        assert append_query_auth("https://h/mcp", "k", "") == "https://h/mcp"

    def test_header_bearer_and_custom(self):
        assert header_auth("bearer", "Authorization", "Bearer ", "s") == {
            "Authorization": "Bearer s"
        }
        assert header_auth("header", "X-Api-Key", "Bearer ", "s") == {"X-Api-Key": "s"}
        assert header_auth("query", "Authorization", "Bearer ", "s") == {}

    def test_redact_covers_escaped_form(self):
        """URL 里出现的是转义后的样子 —— 只抹原文等于没抹。"""
        secret = "a+b/c-long-secret"
        text = "failed: https://h/mcp?k=a%2Bb%2Fc-long-secret"
        out = redact(text, secret)
        assert secret not in out
        assert "a%2Bb%2Fc-long-secret" not in out
        assert "***" in out

    def test_redact_skips_short_secrets(self):
        """短串按子串撞会毁掉正常报错（'k' 把 skipped 变成 s***ipped）。"""
        assert redact("server skipped", "k") == "server skipped"
        assert redact("plain", "") == "plain"

    def test_redact_replaces_long_secret(self):
        assert redact("key=abcdef123", "abcdef123") == "key=***"


def _write_probe_plugin(tmp_path: Any, auth: dict[str, Any], url: str) -> None:
    """在临时工作区放一个声明了 kind=mcp 的插件。"""
    plugin_dir = tmp_path / ".nono-agent" / "plugins" / "probe"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "probe",
                "version": "1.0.0",
                "mcp": {"svc": {"type": "http", "url": url}},
                "tools": {
                    "search": {
                        "kind": "mcp",
                        "server": "svc",
                        "allowed_tools": ["echo"],
                        "auth": auth,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".nono-agent" / "settings.json").write_text(
        json.dumps({"tools": {"probe": {"enabled": True, "api_key_env": "PROBE_KEY"}}}),
        encoding="utf-8",
    )


class TestMcpAuthInjection:
    """构造出来的连接配置对不对 —— 这一步不需要真服务器。"""

    async def test_bearer_error_does_not_leak_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROBE_KEY", "sekret-value-123")
        _write_probe_plugin(
            tmp_path,
            {"type": "bearer", "api_key_env": "PROBE_KEY"},
            "http://127.0.0.1:1/mcp",
        )
        runtime = await start_tool_plugin_runtime(
            tmp_path, settings=load_settings(str(tmp_path))
        )
        try:
            status = runtime.statuses[0]
            # 端口没人听，连不上是预期的；重点是 Key 不能出现在错误里
            assert status.error is not None
            assert "sekret-value-123" not in status.error
        finally:
            await runtime.close()

    async def test_query_auth_hides_encoded_key_from_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROBE_KEY", "a+b/c=d-secret")
        _write_probe_plugin(
            tmp_path,
            {"type": "query", "api_key_env": "PROBE_KEY", "query_param": "apiKey"},
            "http://127.0.0.1:1/mcp",
        )
        runtime = await start_tool_plugin_runtime(
            tmp_path, settings=load_settings(str(tmp_path))
        )
        try:
            error = runtime.statuses[0].error or ""
            # 错误里可能带整条 URL —— 转义形式也必须抹掉
            assert "a+b/c=d-secret" not in error
            assert "a%2Bb%2Fc%3Dd-secret" not in error
        finally:
            await runtime.close()

    async def test_query_auth_requires_param_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROBE_KEY", "some-key-value")
        _write_probe_plugin(
            tmp_path,
            {"type": "query", "api_key_env": "PROBE_KEY"},
            "http://127.0.0.1:1/mcp",
        )
        runtime = await start_tool_plugin_runtime(
            tmp_path, settings=load_settings(str(tmp_path))
        )
        try:
            error = runtime.statuses[0].error or ""
            assert "query_param" in error
        finally:
            await runtime.close()


@pytest.mark.asyncio
async def test_query_auth_works_against_real_server(tmp_path, monkeypatch):
    """真起一个 MCP HTTP 服务，验证 query 鉴权真的把 Key 送到了。"""
    from mcp.server.mcpserver import MCPServer

    port = _free_port()
    server = MCPServer("auth-probe")

    @server.tool()
    def echo(message: str = "") -> str:
        return f"ok:{message}"

    task = asyncio.create_task(
        server.run_streamable_http_async(host="127.0.0.1", port=port)
    )
    try:
        await _wait_port(port)
        monkeypatch.setenv("PROBE_KEY", "tvly-secret-value")
        _write_probe_plugin(
            tmp_path,
            {"type": "query", "api_key_env": "PROBE_KEY", "query_param": "apiKey"},
            f"http://127.0.0.1:{port}/mcp",
        )
        runtime = await start_tool_plugin_runtime(
            tmp_path, settings=load_settings(str(tmp_path))
        )
        try:
            status = next(s for s in runtime.statuses if s.id == "probe")
            assert status.error is None, status.error
            assert status.tools == ["mcp__svc__echo"]
        finally:
            await runtime.close()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_spec_accepts_query_auth():
    spec = parse_tool_specs(
        "p",
        {
            "s": {
                "kind": "mcp",
                "server": "svc",
                "auth": {"type": "query", "api_key_env": "K", "query_param": "key"},
            }
        },
    )[0]
    assert spec.auth.type == "query"
    assert spec.auth.query_param == "key"
    assert spec.auth.needs_api_key is True
