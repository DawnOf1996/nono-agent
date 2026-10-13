"""产品层 SQLite：工作区与会话列表映射。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.common.store import AppStore  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.features.router import mount_product_routes  # noqa: E402


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

    def test_rename_workspace_survives_upsert(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        created = store.upsert_workspace(str(tmp_path / "proj"))
        renamed = store.update_workspace(created["id"], name="前端")
        assert renamed is not None
        assert renamed["name"] == "前端"
        again = store.upsert_workspace(str(tmp_path / "proj"))
        assert again["id"] == created["id"]
        assert again["name"] == "前端"

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

    def test_side_session_stays_out_of_the_main_list(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        workspace = store.upsert_workspace(str(tmp_path))
        parent = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id="parent",
            title="主会话",
        )
        side = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id="side",
            title="侧边对话",
        )
        assert store.set_side_of(side["id"], parent["id"])["side_of"] == parent["id"]
        listed = [item["id"] for item in store.list_sessions()]
        assert parent["id"] in listed
        assert side["id"] not in listed
        hidden = store.list_sessions(side_of=parent["id"])
        assert [item["id"] for item in hidden] == [side["id"]]
        promoted = store.set_side_of(side["id"], None)
        assert promoted is not None
        assert promoted["side_of"] in {None, ""}
        assert side["id"] in [item["id"] for item in store.list_sessions()]

    def test_list_forks_keeps_archived_and_skips_side_sessions(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        workspace = store.upsert_workspace(str(tmp_path))
        root = store.create_session(
            workspace_id=workspace["id"], engine_session_id="root", title="原会话"
        )
        archived = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id="fork-1",
            title="1 - 原会话",
            fork_root=root["id"],
        )
        side = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id="side-1",
            title="侧边对话",
            fork_root=root["id"],
        )
        store.set_side_of(side["id"], root["id"])
        # 归档只是收起，不是删掉：编号不能被回收，否则列表里会突然冒出两个「1 - 」
        assert store.update_session(archived["id"], archived=True) is not None
        forks = store.list_forks(root["id"])
        assert [item["id"] for item in forks] == [archived["id"]]
        assert forks[0]["archived"] is True
        assert store.list_forks("nobody") == []

    def test_open_legacy_db_gains_fork_root_column(self, tmp_path: Path):
        """老库没有 fork_root 列，照样要能打开。

        建表语句是 CREATE TABLE IF NOT EXISTS：表已存在就是空操作，列得靠
        ALTER 补，而建在这列上的索引必须排在 ALTER 之后，否则开库直接炸。
        """
        db_path = tmp_path / "app.db"
        legacy = sqlite3.connect(str(db_path))
        legacy.executescript(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                path_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                engine_session_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                permission_mode TEXT NOT NULL DEFAULT 'default',
                turn_count INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                side_of TEXT,
                cwd TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            """
        )
        legacy.commit()
        legacy.close()

        store = AppStore(db_path)
        workspace = store.upsert_workspace(str(tmp_path))
        root = store.create_session(
            workspace_id=workspace["id"], engine_session_id="e1", title="原会话"
        )
        assert root["fork_root"] is None
        forked = store.create_session(
            workspace_id=workspace["id"],
            engine_session_id="e2",
            title="1 - 原会话",
            fork_root=root["id"],
        )
        assert [item["id"] for item in store.list_forks(root["id"])] == [forked["id"]]
        indexes = {
            row[0]
            for row in sqlite3.connect(str(db_path))
            .execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            .fetchall()
        }
        assert "idx_sessions_fork_root" in indexes

    def test_delete_workspace_moves_sessions_to_ungrouped(self, tmp_path: Path):
        store = AppStore(tmp_path / "app.db")
        workspace = store.upsert_workspace(str(tmp_path))
        row = store.create_session(workspace_id=workspace["id"], engine_session_id="e1")
        assert store.delete_workspace(workspace["id"])
        assert store.list_workspaces() == []
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == row["id"]
        assert sessions[0]["engine_session_id"] == "e1"
        assert sessions[0]["cwd"] == workspace["path"]
        assert sessions[0]["workspace_id"] != workspace["id"]
        with pytest.raises(ValueError, match="ungrouped"):
            store.delete_workspace(sessions[0]["workspace_id"])

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
            patched = client.patch(
                f"/app/workspaces/{workspace['id']}",
                json={"name": "我的项目"},
            )
            assert patched.status_code == 200
            assert patched.json()["name"] == "我的项目"
            created = client.post("/app/workspaces", json={"path": str(tmp_path)})
            assert created.json()["name"] == "我的项目"
            deleted = client.delete(f"/app/workspaces/{workspace['id']}")
            assert deleted.status_code == 200
            assert client.get("/app/workspaces").json()["workspaces"] == []

    def test_delete_workspace_keeps_sessions_ungrouped(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions",
                json={"workspace_id": workspace["id"], "title": "还在"},
            ).json()
            deleted = client.delete(f"/app/workspaces/{workspace['id']}")
            assert deleted.status_code == 200
            assert client.get("/app/workspaces").json()["workspaces"] == []
            listed = client.get("/app/sessions").json()["sessions"]
            assert len(listed) == 1
            assert listed[0]["id"] == row["id"]
            assert listed[0]["title"] == "还在"
            assert listed[0]["cwd"] == row["cwd"]
            assert listed[0]["workspace_id"] != workspace["id"]

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

    def test_archive_hides_session_from_list(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"], "title": "旧对话"}
            ).json()
            archived = client.patch(
                f"/app/sessions/{row['id']}",
                json={"archived": True},
            )
            assert archived.status_code == 200
            assert archived.json()["archived"] is True
            assert client.get("/app/sessions").json()["sessions"] == []
            listed_all = client.get("/app/sessions?archived=all").json()["sessions"]
            assert [item["id"] for item in listed_all] == [row["id"]]
            assert listed_all[0]["archived"] is True
            only = client.get("/app/sessions?archived=only").json()["sessions"]
            assert [item["id"] for item in only] == [row["id"]]
            restored = client.patch(
                f"/app/sessions/{row['id']}",
                json={"archived": False},
            )
            assert restored.status_code == 200
            assert restored.json()["archived"] is False
            sessions = client.get("/app/sessions").json()["sessions"]
            assert [item["id"] for item in sessions] == [row["id"]]
            assert client.get("/app/sessions?archived=only").json()["sessions"] == []
            assert client.get("/app/sessions?archived=nope").status_code == 400

    def test_fork_session_numbers_copies(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"], "title": "原会话"}
            ).json()
            forked = client.post(f"/app/sessions/{row['id']}/fork")
            assert forked.status_code == 200
            copy = forked.json()
            assert copy["id"] != row["id"]
            assert copy["engine_session_id"] != row["engine_session_id"]
            # 原会话不带序号，叉分从 1 起 —— 有序号的就是叉分出来的
            assert copy["title"] == "1 - 原会话"
            assert copy["fork_root"] == row["id"]
            again = client.post(f"/app/sessions/{row['id']}/fork").json()
            assert again["title"] == "2 - 原会话"
            listed = client.get("/app/sessions").json()["sessions"]
            assert {item["id"] for item in listed} == {row["id"], copy["id"], again["id"]}
            assert {item["title"] for item in listed} == {"原会话", "1 - 原会话", "2 - 原会话"}

    def test_fork_of_fork_stays_in_the_same_family(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"], "title": "原会话"}
            ).json()
            first = client.post(f"/app/sessions/{row['id']}/fork").json()
            forked = client.post(f"/app/sessions/{first['id']}/fork").json()
            # 套娃叉分认同一个根：接着排号，不把前缀叠成「2 - 1 - 原会话」
            assert forked["title"] == "2 - 原会话"
            assert forked["fork_root"] == row["id"]

    def test_fork_of_legacy_copy_restores_the_family_name(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"], "title": "原会话 副本"}
            ).json()
            forked = client.post(f"/app/sessions/{row['id']}/fork").json()
            assert forked["title"] == "1 - 原会话"

    def test_fork_of_untitled_session_still_gets_a_number(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post("/app/sessions", json={"workspace_id": workspace["id"]}).json()
            forked = client.post(f"/app/sessions/{row['id']}/fork").json()
            assert forked["title"] == "1 - 新会话"

    def test_side_session_takes_no_number_until_promoted(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"], "title": "原会话"}
            ).json()
            side = client.post(f"/app/sessions/{row['id']}/side").json()
            assert side["title"] == "侧边对话"
            assert side["fork_root"] == row["id"]
            # 侧边对话不进列表，不该把可见叉分的号往后顶
            visible = client.post(f"/app/sessions/{row['id']}/fork").json()
            assert visible["title"] == "1 - 原会话"
            promoted = client.post(f"/app/sessions/{side['id']}/promote").json()
            assert promoted["title"] == "2 - 原会话"
            assert promoted["fork_root"] == row["id"]

    def test_fork_session_at_boundary_seq(self, tmp_path: Path):
        import asyncio

        from nono_agent.session.events import (
            EVENT_ASSISTANT_MESSAGE,
            EVENT_TURN_END,
            EVENT_TURN_START,
            EVENT_USER_MESSAGE,
            SessionEvent,
        )
        from nono_agent.session.transcript import (
            JSONL_ZSTD_SUFFIX,
            LocalFileTranscriptWriter,
            active_session_dir,
        )

        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions", json={"workspace_id": workspace["id"], "title": "原会话"}
            ).json()
            manager = client.app.state.engine_sessions
            engine = manager.sessions[row["engine_session_id"]]
            writer = engine.transcript_writer
            assert writer is not None

            async def seed() -> None:
                await writer.append_events(
                    [
                        SessionEvent.make(0, EVENT_TURN_START, turn=1),
                        SessionEvent.make(0, EVENT_USER_MESSAGE, content="第一问"),
                        SessionEvent.make(0, EVENT_ASSISTANT_MESSAGE, content="第一答"),
                        SessionEvent.make(0, EVENT_TURN_END, turn=1),
                        SessionEvent.make(0, EVENT_TURN_START, turn=2),
                        SessionEvent.make(0, EVENT_USER_MESSAGE, content="第二问"),
                        SessionEvent.make(0, EVENT_ASSISTANT_MESSAGE, content="第二答"),
                        SessionEvent.make(0, EVENT_TURN_END, turn=2),
                    ]
                )

            asyncio.run(seed())
            forked = client.post(
                f"/app/sessions/{row['id']}/fork",
                json={"boundary_seq": 4},
            )
            assert forked.status_code == 200
            copy = forked.json()
            assert copy["turn_count"] == 1
            child_path = (
                active_session_dir() / f"{copy['engine_session_id']}{JSONL_ZSTD_SUFFIX}"
            )
            child = LocalFileTranscriptWriter(child_path)
            child_events = asyncio.run(child.read_events())
            assert [event.type for event in child_events] == [
                EVENT_TURN_START,
                EVENT_USER_MESSAGE,
                EVENT_ASSISTANT_MESSAGE,
                EVENT_TURN_END,
            ]

    def test_patch_model_updates_live_engine(self, tmp_path: Path):
        with _client(tmp_path) as client:
            workspace = client.post("/app/workspaces", json={"path": str(tmp_path)}).json()
            row = client.post(
                "/app/sessions",
                json={"workspace_id": workspace["id"], "model": "alpha"},
            ).json()
            assert row["model"] == "alpha"
            patched = client.patch(
                f"/app/sessions/{row['id']}",
                json={"model": "beta"},
            )
            assert patched.status_code == 200
            assert patched.json()["model"] == "beta"
            listed = client.get("/app/sessions").json()["sessions"]
            assert listed[0]["model"] == "beta"


class TestSessionModelSwitch:
    @pytest.mark.asyncio
    async def test_get_or_create_applies_model_to_existing_engine(self, tmp_path: Path):
        from nono_app_server.engine.session_manager import SessionManager

        manager = SessionManager(EngineConfig(cwd=str(tmp_path), model="alpha"), None)
        first = await manager.get_or_create("sid", model="alpha", cwd=str(tmp_path))
        try:
            second = await manager.get_or_create("sid", model="beta")
            assert second is first
            assert second._config.model == "beta"
            assert second._config.effective_model == "beta"
        finally:
            await first.close()

    @pytest.mark.asyncio
    async def test_get_or_create_switches_provider(self, tmp_path: Path):
        from nono_agent.providers.catalog import (
            AgentDefaultModel,
            LlmCatalog,
            LlmProviderSection,
            ModelEntry,
            NamedProvider,
            save_credential,
            save_user_catalog,
        )
        from nono_app_server.engine.session_manager import SessionManager

        save_user_catalog(
            LlmCatalog(
                agent_default_model=AgentDefaultModel(provider="minimax", model="MiniMax-M3"),
                llm_provider=LlmProviderSection(
                    providers={
                        "minimax": NamedProvider(
                            display_name="MiniMax",
                            api_key_env="MINIMAX_API_KEY",
                            api="openai_chat_completions",
                            base_url="https://api.minimax.chat/v1",
                            models=[ModelEntry(id="MiniMax-M3")],
                        ),
                        "deepseek": NamedProvider(
                            display_name="DeepSeek",
                            api_key_env="DEEPSEEK_API_KEY",
                            api="openai_responses",
                            base_url="https://api.deepseek.com",
                            models=[ModelEntry(id="DeepSeek-V4-Pro")],
                        ),
                    }
                ),
            )
        )
        save_credential("MINIMAX_API_KEY", "sk-mm")
        save_credential("DEEPSEEK_API_KEY", "sk-ds")
        manager = SessionManager(EngineConfig(cwd=str(tmp_path)), None)
        engine = await manager.get_or_create("sid", model="MiniMax-M3", cwd=str(tmp_path))
        try:
            assert engine._config.effective_model == "MiniMax-M3"
            assert engine._config.provider is not None
            assert engine._config.provider.base_url == "https://api.minimax.chat/v1"
            await manager.get_or_create("sid", model="DeepSeek-V4-Pro")
            assert engine._config.effective_model == "DeepSeek-V4-Pro"
            assert engine._config.provider is not None
            assert engine._config.provider.base_url == "https://api.deepseek.com"
            assert engine._config.provider.adapter == "openai_responses"
        finally:
            await engine.close()


class TestSessionTools:
    @pytest.mark.asyncio
    async def test_web_session_registers_builtin_tools(self, tmp_path: Path):
        from nono_app_server.engine.session_manager import SessionManager

        manager = SessionManager(EngineConfig(cwd=str(tmp_path), model="alpha"), None)
        engine = await manager.get_or_create("sid", cwd=str(tmp_path))
        try:
            names = set(engine.tool_registry.names())
            assert "read_file" in names
            assert "bash" in names
            # web_search 已下线：搜索能力改由工具插件提供，默认不启用
            assert "web_fetch" in names
            assert "web_search" not in names
        finally:
            await engine.close()

