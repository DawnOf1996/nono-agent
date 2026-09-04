"""Tests for the event-sourced transcript backends (LocalFile JSONL / SQLite)."""

import json

import pytest

from nono_agent.paths import resolve_session_cwd
from nono_agent.session import (
    SESSION_FORMAT_VERSION,
    LocalFileTranscriptWriter,
    SessionEvent,
    SQLiteTranscriptWriter,
    VersionMismatchError,
    create_transcript_writer,
    fork_transcript,
    list_session_files,
    make_header,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_TOOL_RESULT,
    EVENT_TURN_START,
    EVENT_USER_MESSAGE,
)


def _events():
    return [
        SessionEvent.make(0, EVENT_TURN_START, turn=1),
        SessionEvent.make(0, EVENT_USER_MESSAGE, content="hello"),
        SessionEvent.make(
            0, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "hi"}]
        ),
    ]


@pytest.fixture(params=["file", "sqlite"])
def writer(request, tmp_path):
    suffix = "jsonl" if request.param == "file" else "db"
    w = create_transcript_writer(request.param, tmp_path / f"session.{suffix}")
    yield w
    close = getattr(w, "close", None)
    if close is not None:
        close()


class TestEventStream:
    @pytest.mark.asyncio
    async def test_append_and_read(self, writer):
        for ev in _events():
            await writer.append_event(ev)

        events = await writer.read_events()
        assert [e.type for e in events] == [
            EVENT_TURN_START,
            EVENT_USER_MESSAGE,
            EVENT_ASSISTANT_MESSAGE,
        ]
        # seq 由后端自增分配
        assert [e.seq for e in events] == [1, 2, 3]
        assert events[1].data["content"] == "hello"

    @pytest.mark.asyncio
    async def test_read_events_from(self, writer):
        for ev in _events():
            await writer.append_event(ev)
        tail = await writer.read_events_from(2)
        assert [e.seq for e in tail] == [2, 3]

    @pytest.mark.asyncio
    async def test_header_roundtrip(self, writer):
        await writer.write_header(make_header("sid-1", cwd="/tmp"))
        got = await writer.read_header()
        assert got is not None
        assert got.session_id == "sid-1"
        assert got.cwd == resolve_session_cwd("/tmp")
        assert got.format_version == SESSION_FORMAT_VERSION

    @pytest.mark.asyncio
    async def test_version_mismatch_rejected(self, writer):
        header = make_header("sid-1")
        header.format_version = SESSION_FORMAT_VERSION + 999
        await writer.write_header(header)
        for ev in _events():
            await writer.append_event(ev)

        with pytest.raises(VersionMismatchError):
            await writer.read_events()

    @pytest.mark.asyncio
    async def test_empty_stream(self, writer):
        assert await writer.read_events() == []
        assert await writer.read_header() is None

    @pytest.mark.asyncio
    async def test_flush_is_safe(self, writer):
        await writer.append_event(SessionEvent.make(0, EVENT_TURN_START, turn=1))
        await writer.flush()
        assert len(await writer.read_events()) == 1


class TestLocalFileSpecifics:
    @pytest.mark.asyncio
    async def test_seq_continues_after_reopen(self, tmp_path):
        path = tmp_path / "session.jsonl"
        first = LocalFileTranscriptWriter(path)
        for ev in _events():
            await first.append_event(ev)

        # 新实例（模拟进程重启）不能把 seq 重置为 1
        second = LocalFileTranscriptWriter(path)
        appended = await second.append_event(
            SessionEvent.make(0, EVENT_USER_MESSAGE, content="again")
        )
        assert appended.seq == 4
        assert [e.seq for e in await second.read_events()] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_header_stored_beside_events(self, tmp_path):
        w = LocalFileTranscriptWriter(tmp_path / "abc.jsonl")
        await w.write_header(make_header("abc"))
        assert w.header_file.name == "abc.header.json"
        assert json.loads(w.header_file.read_text())["session_id"] == "abc"

    @pytest.mark.asyncio
    async def test_truncated_line_skipped(self, tmp_path):
        path = tmp_path / "session.jsonl"
        w = LocalFileTranscriptWriter(path)
        await w.append_event(SessionEvent.make(0, EVENT_USER_MESSAGE, content="hi"))
        with path.open("a", encoding="utf-8") as f:
            f.write('{"seq": 2, "type": "user/mes\n')  # 写入中断的残行

        events = await w.read_events()
        assert [e.data["content"] for e in events] == ["hi"]

    @pytest.mark.asyncio
    async def test_legacy_message_transcript_rejected(self, tmp_path):
        path = tmp_path / "legacy.jsonl"
        path.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
            + "\n",
            encoding="utf-8",
        )
        with pytest.raises(VersionMismatchError):
            await LocalFileTranscriptWriter(path).read_events()


class TestSQLiteSpecifics:
    @pytest.mark.asyncio
    async def test_legacy_messages_table_rejected(self, tmp_path):
        import sqlite3

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE messages (seq INTEGER PRIMARY KEY, data TEXT)")
        conn.commit()
        conn.close()

        w = SQLiteTranscriptWriter(db)
        with pytest.raises(VersionMismatchError):
            await w.read_events()
        w.close()

    @pytest.mark.asyncio
    async def test_delete_clears_events_and_header(self, tmp_path):
        w = SQLiteTranscriptWriter(tmp_path / "session.db")
        await w.write_header(make_header("sid"))
        await w.append_event(SessionEvent.make(0, EVENT_USER_MESSAGE, content="hi"))
        await w.delete()

        assert await w.read_events() == []
        assert await w.read_header() is None
        w.close()


class TestForkTranscript:
    @pytest.mark.asyncio
    async def test_fork_preserves_prefix_and_lineage(self, tmp_path):
        src = SQLiteTranscriptWriter(tmp_path / "src.db")
        dst = SQLiteTranscriptWriter(tmp_path / "dst.db")
        await src.write_header(
            make_header(
                "src-session",
                cwd="/work",
                summary="hello from parentxx",
                permission_mode="plan",
            )
        )

        for ev in [
            *_events(),
            SessionEvent.make(0, EVENT_TOOL_RESULT, call_id="c-1", result="ok"),
        ]:
            await src.append_event(ev)

        await fork_transcript(src, dst, boundary_seq=2, new_session_id="child")

        child_events = await dst.read_events()
        assert [e.type for e in child_events] == [EVENT_TURN_START, EVENT_USER_MESSAGE]
        # 子会话 seq 重新从 1 编号
        assert [e.seq for e in child_events] == [1, 2]

        header = await dst.read_header()
        assert header is not None
        assert header.session_id == "child"
        assert header.parent_session == "src-session"
        assert header.cwd == resolve_session_cwd("/work")
        assert header.summary == "hello from parentxx"
        assert header.permission_mode == "plan"
        src.close()
        dst.close()


class TestFactory:
    def test_file_backend(self, tmp_path):
        assert isinstance(
            create_transcript_writer("file", tmp_path / "s.jsonl"),
            LocalFileTranscriptWriter,
        )

    def test_sqlite_backend(self, tmp_path):
        assert isinstance(
            create_transcript_writer("sqlite", tmp_path / "s.db"),
            SQLiteTranscriptWriter,
        )

    def test_default_backend_is_local_file(self, tmp_path):
        assert isinstance(
            create_transcript_writer(path=tmp_path / "s.jsonl"),
            LocalFileTranscriptWriter,
        )

    def test_unknown_backend(self, tmp_path):
        with pytest.raises(ValueError):
            create_transcript_writer("redis", tmp_path / "x")


class TestListSessionFiles:
    @pytest.mark.asyncio
    async def test_counts_events(self, tmp_path):
        for sid, count in (("a", 2), ("b", 1)):
            w = LocalFileTranscriptWriter(tmp_path / f"{sid}.jsonl")
            for _ in range(count):
                await w.append_event(
                    SessionEvent.make(0, EVENT_USER_MESSAGE, content="hi")
                )

        sessions = list_session_files(tmp_path)
        assert {s["session_id"] for s in sessions} == {"a", "b"}
        assert next(s for s in sessions if s["session_id"] == "a")["event_count"] == 2
        assert "summary" in next(s for s in sessions if s["session_id"] == "a")
        assert "cwd" in next(s for s in sessions if s["session_id"] == "a")

    def test_empty(self, tmp_path):
        assert list_session_files(tmp_path / "nonexistent") == []
