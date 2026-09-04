"""SessionHeader 约束 resume / 接续会话，以及首句摘要。"""

from __future__ import annotations

import json
import sqlite3

from pathlib import Path

import pytest

from nono_agent.adapters.cli import resolve_resume_bindings
from nono_agent.adapters.testing import create_test_engine
from nono_agent.core.config import EngineConfig
from nono_agent.paths import resolve_session_cwd
from nono_agent.permissions.policy import PermissionMode
from nono_agent.session import (
    LocalFileTranscriptWriter,
    SQLiteTranscriptWriter,
    make_header,
    peek_session_header,
    summarize_user_prompt,
)
from nono_agent.session.events import EVENT_USER_INPUT, SESSION_FORMAT_VERSION, SessionEvent
from nono_agent.session.header import SESSION_SUMMARY_MAX_CHARS, SessionHeader


def _skill_md(project) -> None:
    skill_dir = project / ".nono-agent" / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n\nDo alpha.\n",
        encoding="utf-8",
    )


async def _collect(engine, text: str = "hello") -> list:
    msgs = []
    async for msg in engine.submit_message(text):
        msgs.append(msg)
    return msgs


class TestSummarize:
    def test_truncates_to_20_chars(self):
        text = "abcdefghijklmnopqrstuvwxyz"
        assert summarize_user_prompt(text) == text[:SESSION_SUMMARY_MAX_CHARS]
        assert len(summarize_user_prompt(text)) == 20

    def test_strips_and_keeps_short(self):
        assert summarize_user_prompt("  你好  ") == "你好"


class TestPeekAndCliBindings:
    def test_peek_and_cli_header_cwd_wins(self, tmp_path):
        sid = "abc-sid"
        project = tmp_path / "proj"
        project.mkdir()
        header = make_header(sid, cwd=str(project), summary="第一句话摘要")
        (tmp_path / f"{sid}.header.json").write_text(
            json.dumps(header.to_dict(), ensure_ascii=False), encoding="utf-8"
        )
        got = peek_session_header(sid, tmp_path)
        assert got is not None
        assert got.cwd == resolve_session_cwd(project)
        assert got.summary == "第一句话摘要"

        cwd, session_id = resolve_resume_bindings(
            sid, str(tmp_path / "other"), session_dir=str(tmp_path)
        )
        assert cwd == resolve_session_cwd(project)
        assert session_id == sid

    def test_resume_without_header_keeps_cli_cwd(self, tmp_path):
        cwd, session_id = resolve_resume_bindings(
            "missing", str(tmp_path), session_dir=str(tmp_path)
        )
        assert cwd == str(tmp_path)
        assert session_id == "missing"


class TestEngineBindsHeader:
    @pytest.mark.asyncio
    async def test_existing_header_overrides_config_cwd_and_loads_skills(self, tmp_path):
        project = tmp_path / "proj"
        other = tmp_path / "other"
        project.mkdir()
        other.mkdir()
        _skill_md(project)

        writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
        await writer.write_header(make_header("sid", cwd=str(project)))

        engine = create_test_engine(
            responses=[{"text": "ok", "tool_calls": []}],
            transcript_writer=writer,
            config_overrides={"cwd": str(other), "session_id": "sid"},
        )
        async with engine:
            assert engine._config.cwd == resolve_session_cwd(project)
            assert any(s.name == "alpha" for s in engine.skills)

    @pytest.mark.asyncio
    async def test_first_user_message_writes_summary_once(self, tmp_path):
        writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
        long_first = "abcdefghijklmnopqrstuvwxyz"
        engine = create_test_engine(
            responses=[
                {"text": "one", "tool_calls": []},
                {"text": "two", "tool_calls": []},
            ],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path), "session_id": "sid"},
        )
        async with engine:
            await _collect(engine, long_first)
            header = await writer.read_header()
            assert header is not None
            assert header.summary == long_first[:20]
            await _collect(engine, "second message should not replace summary")
            header = await writer.read_header()
            assert header.summary == long_first[:20]

    @pytest.mark.asyncio
    async def test_backfill_summary_from_existing_events(self, tmp_path):
        writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
        await writer.write_header(make_header("sid", cwd=str(tmp_path)))
        await writer.append_event(
            SessionEvent.make(0, EVENT_USER_INPUT, content="backfill-me-please-now")
        )
        engine = create_test_engine(
            responses=[],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path / "other"), "session_id": "sid"},
        )
        async with engine:
            header = await writer.read_header()
            assert header is not None
            assert header.summary == "backfill-me-please-n"
            assert engine._config.cwd == resolve_session_cwd(tmp_path)

    @pytest.mark.asyncio
    async def test_resume_session_applies_header_cwd(self, tmp_path, monkeypatch):
        project = tmp_path / "proj"
        other = tmp_path / "other"
        sessions = tmp_path / "sessions"
        project.mkdir()
        other.mkdir()
        sessions.mkdir()
        monkeypatch.setattr("nono_agent.core.engine.DEFAULT_SESSION_DIR", sessions)

        engine1 = create_test_engine(
            responses=[{"text": "hi", "tool_calls": []}],
            config_overrides={"cwd": str(project), "session_id": "sid"},
        )
        async with engine1:
            await _collect(engine1, "resume-me")

        engine2 = create_test_engine(
            responses=[],
            config_overrides={"cwd": str(other)},
        )
        await engine2.initialize()
        assert engine2._config.cwd == resolve_session_cwd(other)
        await engine2.resume_session("sid")
        assert engine2._config.cwd == resolve_session_cwd(project)
        header = await engine2._transcript_writer.read_header()
        assert header is not None
        assert header.summary == "resume-me"
        await engine2.close()


class TestSqliteHeaderMigration:
    @pytest.mark.asyncio
    async def test_adds_summary_column_on_legacy_db(self, tmp_path):
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.execute(
            """
            CREATE TABLE session_header (
                session_id TEXT PRIMARY KEY,
                format_version INTEGER NOT NULL,
                created_at REAL,
                cwd TEXT,
                parent_session TEXT,
                agent_preset_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO session_header VALUES (?,?,?,?,?,?)",
            ("sid", SESSION_FORMAT_VERSION, 0.0, "/old", None, None),
        )
        conn.commit()
        conn.close()

        writer = SQLiteTranscriptWriter(db)
        header = await writer.read_header()
        assert header is not None
        assert header.cwd == "/old"
        assert header.summary == ""
        header.summary = "legacy-summary-textx"[:20]
        await writer.write_header(header)
        again = await writer.read_header()
        assert again is not None
        assert again.summary == "legacy-summary-textx"[:20]
        writer.close()


class TestAbsoluteCwd:
    def test_make_header_resolves_dot(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        header = make_header("sid", cwd=".")
        assert header.cwd != "."
        assert Path(header.cwd).is_absolute()
        assert header.cwd == resolve_session_cwd(tmp_path)

    def test_engine_config_resolves_dot(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = EngineConfig(cwd=".")
        assert config.cwd != "."
        assert Path(config.cwd).is_absolute()

    @pytest.mark.asyncio
    async def test_legacy_relative_header_rewritten_on_bind(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "other").mkdir()
        writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
        await writer.write_header(
            SessionHeader(
                session_id="sid",
                format_version=SESSION_FORMAT_VERSION,
                created_at=1.0,
                cwd=".",
            )
        )
        engine = create_test_engine(
            responses=[],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path / "other"), "session_id": "sid"},
        )
        async with engine:
            header = await writer.read_header()
            assert header is not None
            assert header.cwd != "."
            assert Path(header.cwd).is_absolute()
            assert engine._config.cwd == resolve_session_cwd(tmp_path)
            assert header.summary == ""


class TestPermissionModeInHeader:
    @pytest.mark.asyncio
    async def test_permission_persists_and_restores_on_resume(self, tmp_path):
        writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
        engine1 = create_test_engine(
            responses=[{"text": "ok", "tool_calls": []}],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path), "session_id": "sid"},
        )
        async with engine1:
            engine1.permission_policy.set_mode(PermissionMode.PLAN)
        header = await writer.read_header()
        assert header is not None
        assert header.permission_mode == "plan"

        engine2 = create_test_engine(
            responses=[],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path), "session_id": "sid"},
        )
        async with engine2:
            assert engine2.permission_policy.mode is PermissionMode.PLAN

    @pytest.mark.asyncio
    async def test_request_mode_writes_header_before_next_turn(self, tmp_path):
        """onchange 就把目标模式写入 header，即使还没跑下一 turn。"""
        import asyncio

        writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
        engine1 = create_test_engine(
            responses=[{"text": "ok", "tool_calls": []}],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path), "session_id": "sid"},
        )
        async with engine1:
            engine1.permission_policy.request_mode(PermissionMode.PLAN)
            assert engine1.permission_policy.mode is PermissionMode.DEFAULT
            if engine1._header_persist_tasks:
                await asyncio.gather(*engine1._header_persist_tasks)

        header = await writer.read_header()
        assert header is not None
        assert header.permission_mode == "plan"
        events = await writer.read_events()
        assert any(e.type == "permission/onchange" for e in events)
        assert not any(e.type == "permission/changed" for e in events)

        engine2 = create_test_engine(
            responses=[],
            transcript_writer=writer,
            config_overrides={"cwd": str(tmp_path), "session_id": "sid"},
        )
        async with engine2:
            assert engine2.permission_policy.mode is PermissionMode.PLAN
            assert engine2.permission_policy.pending_mode is None

    def test_normalize_unknown_falls_back_to_default(self):
        from nono_agent.session.header import normalize_permission_mode

        assert normalize_permission_mode("bypass") == "bypass"
        assert normalize_permission_mode("nope") == "default"
        assert normalize_permission_mode(None) == "default"
