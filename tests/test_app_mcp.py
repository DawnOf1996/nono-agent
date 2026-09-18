"""产品层 MCP：用户级清单 + 工作区开关。"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_agent.mcp.config import load_user_mcp_config  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.product.router import mount_product_routes  # noqa: E402


def _settings(cwd: Path) -> SidecarSettings:
    return SidecarSettings(
        host="127.0.0.1",
        port=8000,
        cwd=str(cwd),
        cors_origins=[],
        catalog_ready=False,
        catalog_error=None,
    )


def _client(tmp_path: Path):
    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        _settings(tmp_path),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=False,
    )
    return fastapi_testclient.TestClient(app)


class TestAppMcp:
    def test_user_level_crud_and_workspace_toggle(self, tmp_path: Path):
        with _client(tmp_path) as client:
            empty = client.get("/app/mcp", params={"cwd": str(tmp_path)}).json()
            assert empty["servers"] == []

            created = client.post(
                "/app/mcp/servers",
                params={"cwd": str(tmp_path)},
                json={
                    "id": "docs",
                    "transport": "http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer x"},
                },
            )
            assert created.status_code == 200
            servers = created.json()["servers"]
            assert len(servers) == 1
            assert servers[0]["id"] == "docs"
            assert servers[0]["enabled"] is False
            assert servers[0]["source"] == "user"
            assert load_user_mcp_config()["docs"].url == "https://example.com/mcp"
            assert not (tmp_path / ".nono-agent" / "mcp.json").exists()

            enabled = client.post(
                "/app/mcp/enabled",
                json={"cwd": str(tmp_path), "id": "docs", "enabled": True},
            )
            assert enabled.status_code == 200
            assert enabled.json()["servers"][0]["enabled"] is True
            settings_text = (tmp_path / ".nono-agent" / "settings.json").read_text(
                encoding="utf-8"
            )
            assert "docs" in settings_text
            assert "true" in settings_text

            deleted = client.delete(
                "/app/mcp/servers/docs", params={"cwd": str(tmp_path)}
            )
            assert deleted.status_code == 200
            assert deleted.json()["servers"] == []
            assert load_user_mcp_config() == {}

    def test_prepare_runtime_streams_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_prepare(command, args, progress=None, **_kwargs):
            assert command == "uvx"
            assert args == ["minimax-coding-plan-mcp"]
            if progress:
                progress("正在下载内置 uv…", 12)
            return {"ok": True, "hosted": True, "kind": "uvx", "executable": "uvx"}

        monkeypatch.setattr(
            "nono_app_server.product.mcp.prepare_stdio_runtime",
            fake_prepare,
        )
        with _client(tmp_path) as client:
            response = client.post(
                "/app/mcp/runtimes/prepare",
                json={"command": "uvx", "args": ["minimax-coding-plan-mcp"]},
            )
            assert response.status_code == 200
            body = response.text
            assert "正在下载内置 uv" in body
            assert "运行时已就绪" in body
            assert "event: done" in body
