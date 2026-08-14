"""Tests for the SQLite transcript backend and factory."""

import pytest

from nono_agent.core.types import UserMessage
from nono_agent.session import SQLiteTranscriptWriter, create_transcript_writer
from nono_agent.session.transcript import LocalFileTranscriptWriter


def _msg(text: str) -> UserMessage:
    return UserMessage(message={"role": "user", "content": text})


class TestSQLiteTranscriptWriter:
    @pytest.mark.asyncio
    async def test_record_and_read(self, tmp_path):
        w = SQLiteTranscriptWriter(tmp_path / "session.db")
        await w.record([_msg("hello"), _msg("world")])

        rows = await w.read()
        assert len(rows) == 2
        assert rows[0]["message"]["content"] == "hello"
        assert rows[1]["message"]["content"] == "world"

    @pytest.mark.asyncio
    async def test_record_append(self, tmp_path):
        w = SQLiteTranscriptWriter(tmp_path / "session.db")
        await w.record_append(_msg("a"))
        await w.record_append(_msg("b"))

        rows = await w.read()
        assert [r["message"]["content"] for r in rows] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_record_overwrites(self, tmp_path):
        w = SQLiteTranscriptWriter(tmp_path / "session.db")
        await w.record([_msg("a"), _msg("b")])
        await w.record([_msg("c")])

        rows = await w.read()
        assert [r["message"]["content"] for r in rows] == ["c"]

    @pytest.mark.asyncio
    async def test_read_range(self, tmp_path):
        w = SQLiteTranscriptWriter(tmp_path / "session.db")
        await w.record([_msg("a"), _msg("b"), _msg("c"), _msg("d")])
        rows = await w.read_range(2, 4)
        assert [r["message"]["content"] for r in rows] == ["b", "c"]

    @pytest.mark.asyncio
    async def test_empty_read(self, tmp_path):
        w = SQLiteTranscriptWriter(tmp_path / "nonexistent" / "s.db")
        assert await w.read() == []


class TestFactory:
    def test_file_backend(self, tmp_path):
        w = create_transcript_writer("file", tmp_path / "s.jsonl")
        assert isinstance(w, LocalFileTranscriptWriter)

    def test_sqlite_backend(self, tmp_path):
        w = create_transcript_writer("sqlite", tmp_path / "s.db")
        assert isinstance(w, SQLiteTranscriptWriter)

    def test_unknown_backend(self, tmp_path):
        with pytest.raises(ValueError):
            create_transcript_writer("redis", tmp_path / "x")
