"""Tests for MCP 2.x client, launcher, runtime, and tool bridge."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import textwrap
from pathlib import Path

import pytest

from nono_agent.mcp import (
    MCPClient,
    MCPServerConfig,
    bridge_mcp_server,
    describe_exception,
    load_mcp_config,
    resolve_launch,
    start_mcp_runtime,
    synthesize_env,
)
from nono_agent.mcp.policy import resolve_server_policy
from nono_agent.mcp.runtime import collect_mcp_servers
from nono_agent.settings import load_settings, load_settings_from_env

_MOCK_SERVER = textwrap.dedent(
    """
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("mock")

    @server.tool()
    def echo() -> str:
        \"\"\"Echo input\"\"\"
        return "ok:echo"

    @server.tool()
    def add() -> str:
        \"\"\"Add numbers\"\"\"
        return "ok:add"

    @server.resource("file:///x.txt")
    def x_txt() -> str:
        return "DATA"

    if __name__ == "__main__":
        server.run(transport="stdio")
    """
)


@pytest.fixture
def mock_server_script(tmp_path: Path) -> Path:
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(_MOCK_SERVER, encoding="utf-8")
    return script


def _stdio_config(script: Path, name: str = "mock") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        command=sys.executable,
        args=["-u", str(script)],
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TestDescribeException:
    def test_unwraps_taskgroup_message(self) -> None:
        inner = ConnectionError("All connection attempts failed")
        group = ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)", [inner]
        )
        text = describe_exception(group)
        assert "All connection attempts failed" in text
        assert "TaskGroup" not in text

    def test_keeps_http_status_text(self) -> None:
        inner = RuntimeError("Client error '404 Not Found' for url 'http://127.0.0.1:8765/mcp'")
        wrapped = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [inner])
        assert "404 Not Found" in describe_exception(wrapped)

    def test_does_not_repeat_already_wrapped_message(self) -> None:
        from nono_agent.mcp.client import MCPClientError

        inner = RuntimeError("Not Found")
        group = ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)", [inner]
        )
        wrapped = MCPClientError(
            f"Failed to start MCP server 'username': {describe_exception(group)}"
        )
        wrapped.__cause__ = group
        text = describe_exception(wrapped)
        assert text.count("Not Found") == 1
        assert "TaskGroup" not in text


class TestLoadMCPConfig:
    def test_load(self, tmp_path: Path) -> None:
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "filesystem": {
                            "command": "npx",
                            "args": ["-y", "fs"],
                            "type": "stdio",
                            "enabled": False,
                            "token": "should-not-load",
                        },
                        "remote": {"type": "sse", "url": "https://x/mcp"},
                        "cloud": {
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "Bearer abc"},
                        },
                        "demo": {
                            "runtime": "python",
                            "entrypoint": "server.py",
                            "requirements": "requirements.txt",
                        },
                    }
                }
            )
        )
        servers = load_mcp_config(cfg)
        assert servers["filesystem"].command == "npx"
        assert not hasattr(servers["filesystem"], "enabled")
        assert not hasattr(servers["filesystem"], "token")
        assert servers["remote"].transport == "sse"
        assert servers["remote"].normalized_transport == "sse"
        assert servers["cloud"].normalized_transport == "http"
        assert servers["cloud"].headers["Authorization"] == "Bearer abc"
        assert servers["demo"].runtime == "python"
        assert servers["demo"].entrypoint == "server.py"

    def test_missing(self, tmp_path: Path) -> None:
        assert load_mcp_config(tmp_path / "nope.json") == {}

    def test_url_without_type_is_http(self, tmp_path: Path) -> None:
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"remote": {"url": "https://example.com/mcp"}}})
        )
        servers = load_mcp_config(cfg)
        assert servers["remote"].normalized_transport == "http"


class TestLauncher:
    def test_user_env_overlays_default(self) -> None:
        env = synthesize_env({"MY_KEY": "1", "PYTHONPATH": "/allowed"})
        assert env["MY_KEY"] == "1"
        assert env["PYTHONPATH"] == "/allowed"
        assert "PATH" in env

    def test_resolve_http(self, tmp_path: Path) -> None:
        recipe = resolve_launch(
            MCPServerConfig(
                name="remote",
                transport="http",
                url="https://example.com/mcp",
                headers={"Authorization": "Bearer abc"},
            ),
            tmp_path,
        )
        assert recipe.transport == "http"
        assert recipe.url == "https://example.com/mcp"
        assert recipe.headers["Authorization"] == "Bearer abc"

    def test_resolve_stdio_keeps_user_command(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "mcp-data"
        recipe = resolve_launch(
            MCPServerConfig(
                name="demo",
                command=sys.executable,
                args=["-u", "server.py"],
                env={"MY_KEY": "1"},
            ),
            tmp_path,
            data_dir=data_dir,
        )
        assert recipe.transport == "stdio"
        assert recipe.argv == [sys.executable, "-u", "server.py"]
        assert recipe.env["MY_KEY"] == "1"
        assert "PYTHONNOUSERSITE" not in recipe.env
        assert recipe.stderr_log == data_dir / "logs" / "demo.stderr.log"
        assert recipe.stderr_log is not None
        assert recipe.stderr_log.parent.exists()

    def test_resolve_runtime_entrypoint_uses_path_names(self, tmp_path: Path) -> None:
        recipe = resolve_launch(
            MCPServerConfig(
                name="demo",
                runtime="python",
                entrypoint="server.py",
            ),
            tmp_path,
            data_dir=tmp_path / "mcp-data",
        )
        assert recipe.argv[0] == "python"
        assert recipe.argv[-1].endswith("server.py")
        assert "-u" in recipe.argv

    def test_resolve_bare_npx_unchanged(self, tmp_path: Path) -> None:
        recipe = resolve_launch(
            MCPServerConfig(name="fs", command="npx", args=["-y", "fs"]),
            tmp_path,
            data_dir=tmp_path / "mcp-data",
        )
        assert recipe.argv == ["npx", "-y", "fs"]
        assert "npm_config_prefix" not in recipe.env

    def test_env_file_then_env_overlay(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_FILE=1\nOVERLAY=file\n", encoding="utf-8")
        recipe = resolve_launch(
            MCPServerConfig(
                name="demo",
                command="python",
                args=["server.py"],
                env_file=str(env_file),
                env={"OVERLAY": "config"},
            ),
            tmp_path,
            data_dir=tmp_path / "mcp-data",
        )
        assert recipe.env["FROM_FILE"] == "1"
        assert recipe.env["OVERLAY"] == "config"


class TestMCPClient:
    @pytest.mark.asyncio
    async def test_lifecycle_and_tools(self, mock_server_script: Path, tmp_path: Path) -> None:
        client = MCPClient(_stdio_config(mock_server_script), workspace=tmp_path, data_dir=tmp_path)
        async with client:
            tools = await client.list_tools()
            assert {t["name"] for t in tools} == {"echo", "add"}
            result = await client.call_tool("echo", {})
            assert result == "ok:echo"

    @pytest.mark.asyncio
    async def test_resources(self, mock_server_script: Path, tmp_path: Path) -> None:
        client = MCPClient(_stdio_config(mock_server_script), workspace=tmp_path, data_dir=tmp_path)
        async with client:
            resources = await client.list_resources()
            assert any(item.get("uri") == "file:///x.txt" for item in resources)
            text = await client.read_resource("file:///x.txt")
            assert text == "DATA"


class TestBridge:
    @pytest.mark.asyncio
    async def test_bridge_mcp_server(self, mock_server_script: Path, tmp_path: Path) -> None:
        tools, client = await bridge_mcp_server(
            _stdio_config(mock_server_script),
            workspace=str(tmp_path),
            data_dir=tmp_path,
        )
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


class TestRuntime:
    @pytest.mark.asyncio
    async def test_start_runtime_registers_and_isolates_failures(
        self, mock_server_script: Path, tmp_path: Path
    ) -> None:
        project = tmp_path / ".nono-agent"
        project.mkdir()
        (project / "mcp.json").write_text(
            json.dumps(
                {
                    "servers": {
                        "mock": {
                            "command": sys.executable,
                            "args": ["-u", str(mock_server_script)],
                        },
                        "broken": {"transport": "http"},
                    }
                }
            )
        )
        settings = load_settings(tmp_path, user_settings={}, project_settings={})
        runtime = await start_mcp_runtime(
            tmp_path, settings=settings, data_dir=tmp_path / "mcp-data"
        )
        try:
            assert runtime.enabled
            names = {t.name for t in runtime.tools}
            assert "mcp__mock__echo" in names
            by_name = {s.name: s for s in runtime.statuses}
            assert by_name["mock"].running
            assert by_name["broken"].running is False
            assert by_name["broken"].error
            assert "requires a url" in by_name["broken"].error
            assert "TaskGroup" not in by_name["broken"].error
        finally:
            await runtime.close()

    @pytest.mark.asyncio
    async def test_disabled_does_not_spawn(
        self, mock_server_script: Path, tmp_path: Path
    ) -> None:
        project = tmp_path / ".nono-agent"
        project.mkdir()
        (project / "mcp.json").write_text(
            json.dumps(
                {
                    "servers": {
                        "mock": {
                            "command": sys.executable,
                            "args": ["-u", str(mock_server_script)],
                        }
                    }
                }
            )
        )
        settings = load_settings(
            tmp_path,
            user_settings={},
            project_settings={"mcp": {"enabled": False}},
        )
        runtime = await start_mcp_runtime(tmp_path, settings=settings)
        assert runtime.enabled is False
        assert runtime.tools == []
        assert runtime.clients == []

    @pytest.mark.asyncio
    async def test_per_server_settings_disable(
        self, mock_server_script: Path, tmp_path: Path
    ) -> None:
        project = tmp_path / ".nono-agent"
        project.mkdir()
        (project / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "mock": {
                            "command": sys.executable,
                            "args": ["-u", str(mock_server_script)],
                        }
                    }
                }
            )
        )
        settings = load_settings(
            tmp_path,
            user_settings={},
            project_settings={"mcp": {"servers": {"mock": {"enabled": False}}}},
        )
        runtime = await start_mcp_runtime(
            tmp_path, settings=settings, data_dir=tmp_path / "mcp-data"
        )
        try:
            assert runtime.enabled
            assert runtime.tools == []
            assert runtime.clients == []
            assert runtime.statuses[0].enabled is False
            assert runtime.statuses[0].running is False
        finally:
            await runtime.close()

    @pytest.mark.asyncio
    async def test_disabled_tools_are_not_registered(
        self, mock_server_script: Path, tmp_path: Path
    ) -> None:
        project = tmp_path / ".nono-agent"
        project.mkdir()
        (project / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "mock": {
                            "command": sys.executable,
                            "args": ["-u", str(mock_server_script)],
                        }
                    }
                }
            )
        )
        settings = load_settings(
            tmp_path,
            user_settings={},
            project_settings={
                "mcp": {"servers": {"mock": {"disabledTools": ["add"]}}}
            },
        )
        runtime = await start_mcp_runtime(
            tmp_path, settings=settings, data_dir=tmp_path / "mcp-data"
        )
        try:
            names = {t.name for t in runtime.tools}
            assert "mcp__mock__echo" in names
            assert "mcp__mock__add" not in names
            assert runtime.statuses[0].disabled_tools == ["add"]
        finally:
            await runtime.close()

    def test_collect_includes_plugins(self, tmp_path: Path) -> None:
        plugin = tmp_path / ".nono-agent" / "plugins" / "p"
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "p",
                    "version": "1.0.0",
                    "mcp": {"from-plugin": {"command": "npx", "args": ["x"]}},
                }
            )
        )
        servers = collect_mcp_servers(tmp_path)
        assert "from-plugin" in servers


class TestStreamableHTTP:
    @pytest.mark.asyncio
    async def test_http_bridge(self, tmp_path: Path) -> None:
        from mcp.server.mcpserver import MCPServer

        port = _free_port()
        server = MCPServer("http-mock")

        @server.tool()
        def echo(message: str = "") -> str:
            return f"ok:{message}"

        task = asyncio.create_task(
            server.run_streamable_http_async(host="127.0.0.1", port=port)
        )
        try:
            await _wait_port(port)
            tools, client = await bridge_mcp_server(
                MCPServerConfig(
                    name="remote",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/mcp",
                ),
                workspace=str(tmp_path),
            )
            try:
                echo_tool = next(t for t in tools if t.name == "mcp__remote__echo")
                result = await echo_tool.execute({"message": "hi"}, None)
                assert not result.is_error
                assert result.content == "ok:hi"
            finally:
                await client.close()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_http_404_message_is_readable(self, tmp_path: Path) -> None:
        from mcp.server.mcpserver import MCPServer

        from nono_agent.mcp.client import MCPClientError

        port = _free_port()
        server = MCPServer("http-mock")
        task = asyncio.create_task(
            server.run_streamable_http_async(host="127.0.0.1", port=port)
        )
        try:
            await _wait_port(port)
            client = MCPClient(
                MCPServerConfig(
                    name="username",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/not-mcp",
                ),
                workspace=str(tmp_path),
            )
            with pytest.raises(MCPClientError) as caught:
                await client.start()
            text = str(caught.value)
            assert "TaskGroup" not in text
            assert "404" in text or "Not Found" in text
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class TestEngineWiring:
    @pytest.mark.asyncio
    async def test_engine_registers_mcp_tools(
        self, mock_server_script: Path, tmp_path: Path
    ) -> None:
        from nono_agent.core.config import EngineConfig
        from nono_agent.core.engine import QueryEngine

        project = tmp_path / ".nono-agent"
        project.mkdir()
        (project / "settings.json").write_text(json.dumps({"mcp": {"enabled": True}}))
        (project / "mcp.json").write_text(
            json.dumps(
                {
                    "servers": {
                        "mock": {
                            "command": sys.executable,
                            "args": ["-u", str(mock_server_script)],
                        }
                    }
                }
            )
        )
        engine = QueryEngine(EngineConfig(cwd=str(tmp_path), model="claude-test"))
        async with engine:
            assert engine.tool_registry.has("mcp__mock__echo")
            assert engine.mcp_runtime is not None
            assert engine.mcp_runtime.enabled

    @pytest.mark.asyncio
    async def test_engine_respects_env_flag(
        self, mock_server_script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nono_agent.core.config import EngineConfig
        from nono_agent.core.engine import QueryEngine

        project = tmp_path / ".nono-agent"
        project.mkdir()
        (project / "mcp.json").write_text(
            json.dumps(
                {
                    "servers": {
                        "mock": {
                            "command": sys.executable,
                            "args": ["-u", str(mock_server_script)],
                        }
                    }
                }
            )
        )
        monkeypatch.setenv("NONO_MCP_ENABLED", "0")
        engine = QueryEngine(EngineConfig(cwd=str(tmp_path), model="claude-test"))
        async with engine:
            assert not engine.tool_registry.has("mcp__mock__echo")
            assert engine.mcp_runtime is not None
            assert engine.mcp_runtime.enabled is False


class TestSettingsMcpFlag:
    def test_default_enabled(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path, user_settings={}, project_settings={})
        assert settings.get("mcp.enabled") is True

    def test_env_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NONO_MCP_ENABLED", "0")
        settings = load_settings_from_env(tmp_path)
        assert settings.get("mcp.enabled") is False
        assert settings.source_of("mcp.enabled") == "flag"

    def test_per_server_policy(self, tmp_path: Path) -> None:
        settings = load_settings(
            tmp_path,
            user_settings={},
            project_settings={
                "mcp": {
                    "servers": {
                        "calc": {
                            "enabled": False,
                            "disabledTools": ["divide", "mcp__calc__add"],
                        }
                    }
                }
            },
        )
        policy = resolve_server_policy(settings, "calc")
        assert policy.enabled is False
        assert policy.disabled_tools == frozenset({"divide", "add"})
        assert resolve_server_policy(settings, "other").enabled is True


async def _wait_port(port: int, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.05)
