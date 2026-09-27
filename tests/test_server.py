"""Tests for the server adapter (auth / AskUser answer)."""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")


from nono_app_server.engine import create_server_app  # noqa: E402


def _client(**kwargs):
    app = create_server_app(**kwargs)
    return fastapi_testclient.TestClient(app)


class TestAuth:
    def test_health_unauthenticated(self):
        with _client(api_key="secret") as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_protected_requires_key(self):
        with _client(api_key="secret") as client:
            resp = client.get("/sessions")
            assert resp.status_code == 401

    def test_protected_with_key(self):
        with _client(api_key="secret") as client:
            resp = client.get("/sessions", headers={"X-API-Key": "secret"})
            assert resp.status_code == 200

    def test_protected_with_bearer(self):
        with _client(api_key="secret") as client:
            resp = client.get("/sessions", headers={"Authorization": "Bearer secret"})
            assert resp.status_code == 200

    def test_no_auth_when_no_key(self):
        with _client(api_key=None) as client:
            resp = client.get("/sessions")
            assert resp.status_code == 200


class TestSlashCommands:
    def test_list_includes_mcp(self):
        with _client(api_key=None) as client:
            data = client.get("/commands").json()
        names = {item["name"] for item in data["commands"]}
        assert "mcp" in names
        assert "compact" in names
        assert "help" in names

    def test_run_mcp_without_session(self, tmp_path):
        with _client(api_key=None) as client:
            resp = client.post("/command", json={"line": "/mcp", "cwd": str(tmp_path)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "mcp"
        assert "MCP" in body["result"]

    def test_invalid_line_rejected(self):
        with _client(api_key=None) as client:
            resp = client.post("/command", json={"line": "/"})
        assert resp.status_code == 400


class TestAskUserAnswer:
    def test_no_pending_question(self):
        with _client(api_key=None) as client:
            resp = client.post("/session/x/answer", json={"answers": {}})
            assert resp.status_code == 404


class TestPermissionMode:
    def test_new_session_accepts_mode(self, tmp_path):
        with _client(api_key=None) as client:
            resp = client.post(
                "/session/new",
                json={"cwd": str(tmp_path), "permission_mode": "plan"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["permission_mode"] == "plan"
            sid = data["session_id"]

            listed = client.get("/sessions").json()["sessions"]
            assert sid not in {item["session_id"] for item in listed}

            switched = client.post(f"/session/{sid}/permission", json={"mode": "bypass"})
            assert switched.status_code == 200
            assert switched.json()["effective"] == "bypass"
            still = client.get("/sessions").json()["sessions"]
            assert sid not in {item["session_id"] for item in still}

    def test_unknown_mode_rejected(self, tmp_path):
        with _client(api_key=None) as client:
            created = client.post("/session/new", json={"cwd": str(tmp_path)})
            sid = created.json()["session_id"]
            resp = client.post(f"/session/{sid}/permission", json={"mode": "yolo"})
            assert resp.status_code == 400


class TestNewSession:
    def test_empty_post_returns_cwd(self):
        with _client(api_key=None) as client:
            resp = client.post("/session/new")
            assert resp.status_code == 200
            data = resp.json()
            assert data["session_id"]
            assert data["cwd"]
            assert "model" in data
            assert data["summary"] == ""
            listed = client.get("/sessions").json()["sessions"]
            assert data["session_id"] not in {item["session_id"] for item in listed}

    def test_json_cwd(self, tmp_path):
        with _client(api_key=None) as client:
            resp = client.post("/session/new", json={"cwd": str(tmp_path)})
            assert resp.status_code == 200
            cwd = resp.json()["cwd"].replace("\\", "/").lower()
            assert tmp_path.name.lower() in cwd

    def test_empty_session_does_not_write_files(self, tmp_path):
        session_dir = tmp_path / ".nono-agent" / "sessions"
        with _client(api_key=None) as client:
            resp = client.post("/session/new", json={"cwd": str(tmp_path)})
            assert resp.status_code == 200
            sid = resp.json()["session_id"]
            listed = client.get("/sessions").json()["sessions"]
            assert sid not in {item["session_id"] for item in listed}
        if session_dir.exists():
            assert list(session_dir.glob("*.header.json")) == []
            assert list(session_dir.glob("*.jsonl")) == []
            assert list(session_dir.glob("*.jsonl.zst")) == []


class TestListPersistedSessions:
    def test_disk_sessions_appear_without_live_engine(self, tmp_path, monkeypatch):
        import asyncio

        from nono_agent.session import transcript as transcript_module
        from nono_agent.session.events import EVENT_TURN_START, EVENT_USER_MESSAGE, SessionEvent
        from nono_agent.session.header import make_header
        from nono_agent.session.transcript import LocalFileTranscriptWriter

        session_dir = tmp_path / "sessions"
        monkeypatch.setattr(transcript_module, "DEFAULT_SESSION_DIR", session_dir)

        writer = LocalFileTranscriptWriter(session_dir / "abc.jsonl")

        async def write() -> None:
            await writer.write_header(
                make_header("abc", cwd=str(tmp_path), summary="谁是你")
            )
            await writer.append_event(SessionEvent.make(0, EVENT_TURN_START, turn=1))
            await writer.append_event(
                SessionEvent.make(0, EVENT_USER_MESSAGE, content="谁是你")
            )

        asyncio.run(write())

        with _client(api_key=None) as client:
            listed = client.get("/sessions").json()["sessions"]
        match = next(item for item in listed if item["session_id"] == "abc")
        assert match["summary"] == "谁是你"
        assert match["turn_count"] == 1
        assert match["updated_at"] > 0
        assert tmp_path.name.lower() in match["cwd"].replace("\\", "/").lower()


class TestSessionEventChannel:
    """GET /session/{id}/events —— 事件流只读通道（脱敏后推送）。"""

    def _write_session(self, tmp_path, monkeypatch, events):
        import asyncio

        from nono_agent.session import transcript as transcript_module
        from nono_agent.session.events import SessionEvent
        from nono_agent.session.transcript import LocalFileTranscriptWriter

        session_dir = tmp_path / "sessions"
        monkeypatch.setattr(transcript_module, "DEFAULT_SESSION_DIR", session_dir)

        writer = LocalFileTranscriptWriter(session_dir / "sid.jsonl")

        async def write():
            for type_, data in events:
                await writer.append_event(SessionEvent.make(0, type_, **data))

        asyncio.run(write())
        return writer

    def _read_sse(self, client, url):
        with client.stream("GET", url) as resp:
            assert resp.status_code == 200
            return "".join(resp.iter_text())

    def test_missing_session_returns_404(self, tmp_path, monkeypatch):
        self._write_session(tmp_path, monkeypatch, [])
        with _client(api_key=None) as client:
            resp = client.get("/session/nope/events?follow=false")
            assert resp.status_code == 404

    def test_streams_events_and_terminates_without_follow(self, tmp_path, monkeypatch):
        from nono_agent.session.events import EVENT_TURN_END, EVENT_USER_MESSAGE

        self._write_session(
            tmp_path,
            monkeypatch,
            [
                (EVENT_USER_MESSAGE, {"content": "hi"}),
                (EVENT_TURN_END, {"turn": 1, "reason": "COMPLETED"}),
            ],
        )
        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false")

        assert body.count("event: session_event") == 2
        assert "user/message" in body
        assert '"last_seq": 2' in body

    def test_after_seq_resumes_from_cursor(self, tmp_path, monkeypatch):
        from nono_agent.session.events import EVENT_TURN_END, EVENT_USER_MESSAGE

        self._write_session(
            tmp_path,
            monkeypatch,
            [
                (EVENT_USER_MESSAGE, {"content": "hi"}),
                (EVENT_TURN_END, {"turn": 1, "reason": "COMPLETED"}),
            ],
        )
        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false&after_seq=1")

        assert body.count("event: session_event") == 1
        assert "turn/end" in body
        assert "user/message" not in body

    def test_payload_is_redacted(self, tmp_path, monkeypatch):
        from nono_agent.session.events import EVENT_TOOL_RESULT

        secret = "sk-abcdefghijklmnopqrstuvwxyz012345"
        writer = self._write_session(
            tmp_path,
            monkeypatch,
            [(EVENT_TOOL_RESULT, {"call_id": "t1", "result": f"key={secret}"})],
        )
        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false")

        assert secret not in body
        assert "redacted" in body

        # 磁盘上的事件流仍然逐字真实（可能是 zstd 帧，按事件流解码）
        from nono_agent.session.transcript import read_transcript_text

        log = read_transcript_text(writer.session_file)
        assert secret in log

    def test_legacy_log_reports_error_event(self, tmp_path, monkeypatch):
        """事件格式之前的消息副本 transcript 必须报错，而不是静默当空流。"""
        self._write_session(tmp_path, monkeypatch, [])
        session_file = tmp_path / "sessions" / "sid.jsonl"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text('{"role": "user", "content": "old"}\n', encoding="utf-8")

        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false")

        assert "event: error" in body
