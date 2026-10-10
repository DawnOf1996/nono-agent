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

        # 拆成两段：完整字面量会让 GitHub secret scanning 判成 DeepSeek key 泄露，
        # 运行时拼出来的值仍然是完整形态，脱敏测的还是它。见 test_session_redact.py
        # 里同一套写法的说明。
        secret = "sk-abcdefghij" + "klmnopqrstuvwxyz012345"
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

    def _write_chunk_session(self, tmp_path, monkeypatch):
        """写一条同时含思考增量与正文增量的会话。"""
        from nono_agent.session.events import (
            EVENT_ASSISTANT_CHUNK,
            EVENT_TURN_END,
            EVENT_USER_MESSAGE,
        )

        return self._write_session(
            tmp_path,
            monkeypatch,
            [
                (EVENT_USER_MESSAGE, {"content": "hi"}),
                (
                    EVENT_ASSISTANT_CHUNK,
                    {
                        "chunk": {
                            "type": "content_block_delta",
                            "delta": {"type": "thinking_delta", "thinking": "想一下"},
                        }
                    },
                ),
                (
                    EVENT_ASSISTANT_CHUNK,
                    {
                        "chunk": {
                            "type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": "正文分片"},
                        }
                    },
                ),
                (EVENT_TURN_END, {"turn": 1, "reason": "COMPLETED"}),
            ],
        )

    def test_chunks_all_is_the_default_and_keeps_text_deltas(self, tmp_path, monkeypatch):
        self._write_chunk_session(tmp_path, monkeypatch)
        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false")

        assert "thinking_delta" in body
        assert "text_delta" in body

    def test_chunks_thinking_drops_text_deltas_but_keeps_thinking(self, tmp_path, monkeypatch):
        """正文分片在 web 端只会被丢弃，assistant/message 里已有完整正文。"""
        self._write_chunk_session(tmp_path, monkeypatch)
        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false&chunks=thinking")

        assert "thinking_delta" in body
        assert "text_delta" not in body
        # 非分片事件一条都不能少
        assert "user/message" in body
        assert "turn/end" in body
        assert body.count("event: session_event") == 3

    def test_unknown_chunks_mode_falls_back_to_all(self, tmp_path, monkeypatch):
        self._write_chunk_session(tmp_path, monkeypatch)
        with _client(api_key=None) as client:
            body = self._read_sse(client, "/session/sid/events?follow=false&chunks=bogus")

        assert "text_delta" in body

    def test_chunks_thinking_honours_after_seq(self, tmp_path, monkeypatch):
        self._write_chunk_session(tmp_path, monkeypatch)
        with _client(api_key=None) as client:
            body = self._read_sse(
                client, "/session/sid/events?follow=false&chunks=thinking&after_seq=1"
            )

        assert "user/message" not in body
        assert "thinking_delta" in body

    def test_repeated_reads_serve_identical_frames(self, tmp_path, monkeypatch):
        """命中帧缓存后内容必须和首次一致。"""
        from nono_agent.session.events import EVENT_USER_MESSAGE

        self._write_chunk_session(tmp_path, monkeypatch)
        with _client(api_key=None) as client:
            first = self._read_sse(client, "/session/sid/events?follow=false&chunks=thinking")
            second = self._read_sse(client, "/session/sid/events?follow=false&chunks=thinking")

        assert first == second
        assert EVENT_USER_MESSAGE in first

    def test_appended_event_is_served_after_cache_warm(self, tmp_path, monkeypatch):
        """缓存热了以后再追加事件，新事件仍要能读到（缓存按 mtime/大小失效）。"""
        import asyncio as _asyncio

        from nono_agent.session.events import EVENT_USER_MESSAGE, SessionEvent

        writer = self._write_chunk_session(tmp_path, monkeypatch)
        with _client(api_key=None) as client:
            self._read_sse(client, "/session/sid/events?follow=false&chunks=thinking")

        _asyncio.run(
            writer.append_event(SessionEvent.make(0, EVENT_USER_MESSAGE, content="再来一条"))
        )
        with _client(api_key=None) as client:
            third = self._read_sse(client, "/session/sid/events?follow=false&chunks=thinking")

        assert "再来一条" in third

    def test_cache_is_bounded(self, tmp_path):
        """缓存条目数封顶，防止长会话挨个打开把内存吃光。"""
        from nono_app_server.engine import event_frames

        event_frames.clear_cache()
        for i in range(event_frames.CACHE_MAX_ENTRIES + 5):
            f = tmp_path / f"fake-{i}.jsonl"
            f.write_text("{}", encoding="utf-8")
            event_frames.store_frames(f, "all", [(1, "{}")])
        assert len(event_frames._cache) == event_frames.CACHE_MAX_ENTRIES
        event_frames.clear_cache()

    def test_cache_misses_when_file_changes(self, tmp_path):
        """缓存以 (路径, mtime, 大小) 为键，文件被追加后必须重算。"""
        from nono_app_server.engine import event_frames

        event_frames.clear_cache()
        f = tmp_path / "sid.jsonl"
        f.write_text("{}", encoding="utf-8")
        event_frames.store_frames(f, "all", [(1, "old")])
        assert event_frames.cached_frames(f, "all") == [(1, "old")]

        f.write_text('{"seq":1}\n{"seq":2}\n', encoding="utf-8")
        assert event_frames.cached_frames(f, "all") is None
        event_frames.clear_cache()


class TestSessionStats:
    """GET /session/{id}/stats 与 /stats/stream —— 会话统计的一次性读取与推送。"""

    def _write_session(self, tmp_path, monkeypatch, events=()):
        import asyncio

        from nono_agent.session import transcript as transcript_module
        from nono_agent.session.events import SessionEvent
        from nono_agent.session.transcript import LocalFileTranscriptWriter

        session_dir = tmp_path / "sessions"
        monkeypatch.setattr(transcript_module, "DEFAULT_SESSION_DIR", session_dir)
        writer = LocalFileTranscriptWriter(session_dir / "sid.jsonl")

        async def write():
            for type_, data, ts in events:
                await writer.append_event(
                    SessionEvent(seq=0, type=type_, data=data, timestamp=ts)
                )

        asyncio.run(write())
        return writer

    @staticmethod
    def _stream_events():
        from nono_agent.session.events import (
            EVENT_ASSISTANT_CHUNK,
            EVENT_STEP_END,
            EVENT_STEP_START,
            EVENT_TURN_START,
            EVENT_USAGE,
        )

        return [
            (EVENT_TURN_START, {}, 0.0),
            (EVENT_STEP_START, {}, 100.0),
            (EVENT_ASSISTANT_CHUNK, {"chunk": {}}, 200.0),
            (EVENT_ASSISTANT_CHUNK, {"chunk": {}}, 400.0),
            (EVENT_STEP_END, {}, 900.0),
            (
                EVENT_USAGE,
                {
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                    },
                    "model": "m",
                },
                900.0,
            ),
        ]

    def test_stats_reflects_the_log(self, tmp_path, monkeypatch):
        self._write_session(tmp_path, monkeypatch, self._stream_events())
        with _client(api_key=None) as client:
            data = client.get("/session/sid/stats").json()
        assert data["turns"] == 1
        assert data["steps"] == 1
        assert data["usage"]["total_tokens"] == 130
        assert data["model"] == "m"

    def test_stats_404s_for_unknown_session(self, tmp_path, monkeypatch):
        self._write_session(tmp_path, monkeypatch, [])
        with _client(api_key=None) as client:
            assert client.get("/session/nope/stats").status_code == 404

    def test_stream_404s_for_unknown_session(self, tmp_path, monkeypatch):
        self._write_session(tmp_path, monkeypatch, [])
        with _client(api_key=None) as client:
            assert client.get("/session/nope/stats/stream").status_code == 404

    def test_stream_is_registered(self):
        """推送流是无限流：TestClient 读到第一帧就会一直挂着等下一帧，
        所以这里只断言路由存在，真收发在 runtime-test 里对着真服务器验。"""
        from nono_app_server.engine.routers import sessions

        paths = {route.path for route in sessions.router.routes}
        assert "/session/{session_id}/stats/stream" in paths
        assert "/session/{session_id}/stats" in paths


class TestStatsPushFanout:
    """SessionManager 的订阅扇出 —— 消费端不轮询，靠引擎回调。"""

    @staticmethod
    def _manager_with_subscriber(session_id="s1", count=1):
        """直接铺好订阅者集合来测扇出本身。

        走 ``subscribe_stats`` 需要真实存在的会话（它要判断该给谁推），
        那是端点层的职责，这里只关心「有订阅者时怎么推」。
        """
        import asyncio

        from nono_app_server.engine.session_manager import SessionManager

        manager = SessionManager(default_config=None, permission_checker=None)
        queues = [asyncio.Queue(maxsize=8) for _ in range(count)]
        manager._stats_subscribers[session_id] = set(queues)
        return manager, queues

    def test_pushes_reach_every_subscriber(self):
        manager, queues = self._manager_with_subscriber(count=2)
        manager._fanout_stats("s1", {"steps": 3})
        assert all(q.get_nowait() == {"steps": 3} for q in queues)

    def test_unsubscribe_stops_delivery(self):
        import asyncio

        from nono_app_server.engine.session_manager import SessionManager

        manager = SessionManager(default_config=None, permission_checker=None)
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        manager._stats_subscribers["s1"] = {queue}
        manager.unsubscribe_stats("s1", queue)
        manager._fanout_stats("s1", {"steps": 1})
        assert queue.empty()
        assert "s1" not in manager._stats_subscribers

    def test_unsubscribe_keeps_listening_while_others_remain(self):
        import asyncio

        from nono_app_server.engine.session_manager import SessionManager

        manager = SessionManager(default_config=None, permission_checker=None)
        leaving: asyncio.Queue = asyncio.Queue(maxsize=8)
        staying: asyncio.Queue = asyncio.Queue(maxsize=8)
        manager._stats_subscribers["s1"] = {leaving, staying}
        manager.unsubscribe_stats("s1", leaving)
        manager._fanout_stats("s1", {"steps": 2})
        assert leaving.empty()
        assert staying.get_nowait() == {"steps": 2}
        assert manager._stats_subscribers["s1"] == {staying}

    def test_unknown_session_cannot_subscribe(self):
        from nono_app_server.engine.session_manager import SessionManager

        manager = SessionManager(default_config=None, permission_checker=None)
        assert manager.subscribe_stats("nope") is None

    def test_slow_subscriber_drops_oldest_instead_of_blocking(self):
        manager, (queue,) = self._manager_with_subscriber(count=1)
        for step in range(1, 20):  # 远超队列容量
            manager._fanout_stats("s1", {"steps": step})
        # 不能把对话卡住：容量封顶，且留下的一定是最近的。
        assert queue.qsize() <= 8
        assert queue.get_nowait()["steps"] >= 12
