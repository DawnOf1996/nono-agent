"""产品层本机目录浏览与新建文件夹。"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.product.router import mount_product_routes  # noqa: E402


def _client(tmp_path: Path):
    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        SidecarSettings(
            host="127.0.0.1",
            port=8000,
            cwd=str(tmp_path),
            cors_origins=[],
            catalog_ready=False,
            catalog_error=None,
        ),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=False,
    )
    return fastapi_testclient.TestClient(app)


def test_mkdir_creates_folder(tmp_path: Path) -> None:
    parent = tmp_path / "ws"
    parent.mkdir()
    with _client(tmp_path) as client:
        resp = client.post("/app/fs/mkdir", json={"parent": str(parent), "name": "proj"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "proj"
    assert (parent / "proj").is_dir()


def test_mkdir_rejects_invalid_name(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        nested = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": "a/b"})
        colon = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": "a:b"})
        dot = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": ".."})
    assert nested.status_code == 400
    assert colon.status_code == 400
    assert dot.status_code == 400


def test_mkdir_conflict(tmp_path: Path) -> None:
    existing = tmp_path / "taken"
    existing.mkdir()
    with _client(tmp_path) as client:
        resp = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": "taken"})
    assert resp.status_code == 409
