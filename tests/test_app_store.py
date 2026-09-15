"""产品层 SQLite：工作区与会话列表映射。"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.product.router import mount_product_routes  # noqa: E402
from nono_app_server.product.store import AppStore  # noqa: E402


def _settings(cwd: Path) -> SidecarSettings:
    return SidecarSettings(
        host="127.0.0.1",
        port=8000,
        cwd=str(cwd),
        cors_origins=[],
        catalog_ready=False,
        catalog_error=None,
    )


def _client(tmp_path: Path, *, import_engine_index: bool = False):
    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        _settings(tmp_path),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=import_engine_index,
    )
    return fastapi_testclient.TestClient(app)


class TestAppStore:
    def test_upsert_workspace_is_idempotent(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        first = store.upsert_workspace(str(tmp_path / "proj"))
        second = store.upsert_workspace(str(tmp_path / "proj"))
        assert first["id"] == second["id"]
        assert len(store.list_workspaces()) == 1

    def test_session_maps_to_engine_id(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        workspace = store.upsert_workspace(str(tmp_path))
        row = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id="engine-1",
            title="hello",
        )
        assert row["id"] != "engine-1"
        assert row["engine_session_id"] == "engine-1"
        assert row["workspace_id"] == workspace["id"]
        assert store.get_session_by_engine_id("engine-1")["id"] == row["id"]

    def test_delete_workspace_cascades_sessions(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        workspace = store.upsert_workspace(str(tmp_path))
        store.create_session(workspace_id=workspace["id"], engine_session_id="e1")
        assert store.delete_workspace(workspace["id"])
        assert store.list_sessions() == []
        assert store.list_workspaces() == []

    def test_import_engine_index_once(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        items = [
            {
                "session_id": "disk-1",
                "cwd": str(tmp_path / "a"),
                "summary": "谁是你",
                "turn_count": 2,
                "last_modified": 100.0,
                "permission_mode": "plan",
            }
        ]
        assert store.import_engine_index(items) == 1
        assert store.has_imported_engine_index()
        assert store.import_engine_index(items) == 1
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["engine_session_id"] == "disk-1"
        assert sessions[0]["title"] == "谁是你"


class TestProductMappingApi:
    def test_workspace_crud(self, tmp_path: Path):
        with _client(tmp_path) as client:
            created = client.post("/app/workspaces", json={"path": str(tmp_path)})
            assert created.status_code == 200
            workspace = created.json()
            listed = client.get("/app/workspaces").json()["workspaces"]
            assert listed[0]["id"] == workspace["id"]
            again = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            assert again["id"] == workspace["id"]
            deleted = client.delete(f"/app/workspaces/{workspace['id']}")
            assert deleted.status_code == 200
            assert client.get("/app/workspaces").json()["workspaces"] == []

    def test_create_session_list_is_sqlite_not_engine_disk(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            created = client.post(
                "/app/sessions",
                json={"workspace_id": workspace["id"], "permission_mode": "plan"},
            )
            assert created.status_code == 200
            row = created.json()
            assert row["workspace_id"] == workspace["id"]
            assert row["engine_session_id"]
            assert row["id"] != row["engine_session_id"]
            assert row["permission_mode"] == "plan"
            listed = client.get("/app/sessions").json()["sessions"]
            assert listed[0]["id"] == row["id"]
            engine_list = client.get("/sessions").json()["sessions"]
            assert row["engine_session_id"] not in {item["session_id"] for item in engine_list}

    def test_patch_title_and_delete_session(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"]}
            ).json()
            patched = client.patch(
                f"/app/sessions/{row['id']}",
                json={"title": "第一句", "turn_count": 1},
            )
            assert patched.status_code == 200
            assert patched.json()["title"] == "第一句"
            assert patched.json()["turn_count"] == 1
            assert client.delete(f"/app/sessions/{row['id']}").status_code == 200
            assert client.get("/app/sessions").json()["sessions"] == []
