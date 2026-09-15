"""Tests for engine event emission —— 落盘只有事件，消息由 derive 推导。"""

import pytest

from nono_agent.core.types import AssistantMessage, UserMessage
from nono_agent.session import (
    LocalFileTranscriptWriter,
    SQLiteTranscriptWriter,
    derive_messages,
    derived_to_messages,
    verify_reconstruction,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USAGE,
    EVENT_USER_MESSAGE,
)
from nono_agent.session.transcript import JSONL_ZSTD_SUFFIX, ZSTD_MAGIC, decode_transcript_bytes
from tests.helpers import MockTool, create_test_engine


async def _collect(engine):
    msgs = []
    async for msg in engine.submit_message("hello"):
        msgs.append(msg)
    return msgs


@pytest.mark.asyncio
async def test_event_emission_basic(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    engine = create_test_engine(
        responses=[{"text": "hi!", "tool_calls": []}], transcript_writer=writer
    )

    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    types = [e.type for e in events]
    assert EVENT_TURN_START in types
    assert EVENT_TURN_END in types
    assert EVENT_USER_MESSAGE in types
    assert EVENT_ASSISTANT_MESSAGE in types
    assert EVENT_USAGE in types

    # request/header 只写一次（initial）
    headers = [e for e in events if e.type == EVENT_REQUEST_HEADER]
    assert len(headers) == 1
    assert headers[0].data["reason"] == "initial"
    writer.close()


@pytest.mark.asyncio
async def test_default_writer_is_local_file_event_stream(tmp_path):
    """未注入写入器时默认启用本地 JSONL 事件流。"""
    engine = create_test_engine(responses=[{"text": "hi!", "tool_calls": []}])

    async with engine:
        await _collect(engine)

    writer = engine.transcript_writer
    assert isinstance(writer, LocalFileTranscriptWriter)
    assert writer.session_file.name.endswith(JSONL_ZSTD_SUFFIX)
    assert writer.session_file.read_bytes().startswith(ZSTD_MAGIC)

    # 解压后每一行都是事件信封，没有任何消息副本
    lines = [
        line
        for line in decode_transcript_bytes(writer.session_file.read_bytes()).splitlines()
        if line
    ]
    assert lines
    events = await writer.read_events()
    assert len(events) == len(lines)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))

    # 会话元信息落在侧车文件里，携带格式版本
    header = await writer.read_header()
    assert header is not None
    assert header.session_id == engine.session_id


@pytest.mark.asyncio
async def test_event_emission_tool_flow(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    tool = MockTool(name="calc", response="42")
    engine = create_test_engine(
        responses=[
            {
                "text": "",
                "tool_calls": [{"name": "calc", "id": "tc_1", "input": {"q": "6*7"}}],
            },
            {"text": "answer is 42", "tool_calls": []},
        ],
        tools=[tool],
        transcript_writer=writer,
    )

    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    tool_calls = [e for e in events if e.type == EVENT_TOOL_CALL]
    assert tool_calls[0].data["name"] == "calc"
    assert tool_calls[0].data["call_id"] == "tc_1"

    tool_results = [e for e in events if e.type == EVENT_TOOL_RESULT]
    assert tool_results[0].data["call_id"] == "tc_1"
    assert tool_results[0].data["result"] == "42"
    writer.close()


@pytest.mark.asyncio
async def test_derive_messages_roundtrip(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    engine = create_test_engine(
        responses=[{"text": "hi!", "tool_calls": []}], transcript_writer=writer
    )

    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    messages = derive_messages(events)
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles
    verify_reconstruction(events, messages)
    writer.close()


@pytest.mark.asyncio
async def test_request_header_change_on_second_turn(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    engine = create_test_engine(
        responses=[{"text": "a", "tool_calls": []}], transcript_writer=writer
    )

    async with engine:
        await _collect(engine)
        # 修改 system prompt 使 header 变化
        engine._config = engine._config.model_copy(
            update={"append_system_prompt": "CHANGED"}
        )
        await _collect(engine)

    events = await writer.read_events()
    reasons = [e.data["reason"] for e in events if e.type == EVENT_REQUEST_HEADER]
    assert "initial" in reasons
    assert "change" in reasons
    writer.close()


@pytest.mark.asyncio
async def test_resume_via_event_derive(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    engine1 = create_test_engine(
        responses=[{"text": "hi from turn 1", "tool_calls": []}],
        transcript_writer=writer,
    )
    async with engine1:
        await _collect(engine1)

    # 新引擎挂同一条事件流，resume 应通过 derive 重建消息
    engine2 = create_test_engine(responses=[], transcript_writer=writer)
    await engine2.initialize()
    messages = await engine2.resume_session("resumed")

    assert any(isinstance(m, UserMessage) for m in messages)
    assistants = [m for m in messages if isinstance(m, AssistantMessage)]
    content = assistants[-1].message.get("content", [])
    assert any(b.get("text") == "hi from turn 1" for b in content if isinstance(b, dict))
    writer.close()


@pytest.mark.asyncio
async def test_request_header_reason_resume_after_restart(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    engine1 = create_test_engine(
        responses=[{"text": "a", "tool_calls": []}], transcript_writer=writer
    )
    async with engine1:
        await _collect(engine1)

    engine2 = create_test_engine(
        responses=[{"text": "b", "tool_calls": []}], transcript_writer=writer
    )
    async with engine2:
        await _collect(engine2)

    events = await writer.read_events()
    reasons = [e.data["reason"] for e in events if e.type == EVENT_REQUEST_HEADER]
    assert reasons[0] == "initial"
    assert "resume" in reasons
    writer.close()


@pytest.mark.asyncio
async def test_compact_boundary_event_replays_kept_window(tmp_path):
    writer = SQLiteTranscriptWriter(tmp_path / "session.db")
    engine = create_test_engine(
        responses=[{"text": f"reply {i}", "tool_calls": []} for i in range(4)],
        transcript_writer=writer,
    )

    async with engine:
        for _ in range(4):
            await _collect(engine)
        removed = engine.compact(keep_recent=2)

    assert removed > 0
    events = await writer.read_events()
    boundaries = [e for e in events if e.type == EVENT_COMPACT_BOUNDARY]
    assert len(boundaries) == 1
    assert boundaries[0].data["metadata"]["removed"] == removed

    # derive 重建的上下文 == 压缩后的内存 store
    derived = derive_messages(events)
    assert derived[0]["subtype"] == "compact_boundary"
    assert len(derived_to_messages(derived)) == 3  # 边界 + 保留的 2 条
    writer.close()


@pytest.mark.asyncio
async def test_events_survive_process_restart(tmp_path):
    """默认后端换实例接续同一文件：seq 连续、历史完整。"""
    session_file = tmp_path / "s.jsonl"

    engine1 = create_test_engine(
        responses=[{"text": "first", "tool_calls": []}],
        transcript_writer=LocalFileTranscriptWriter(session_file),
    )
    async with engine1:
        await _collect(engine1)

    reopened = LocalFileTranscriptWriter(session_file)
    engine2 = create_test_engine(
        responses=[{"text": "second", "tool_calls": []}],
        transcript_writer=reopened,
    )
    async with engine2:
        await _collect(engine2)

    events = await reopened.read_events()
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    texts = [
        block.get("text")
        for e in events
        if e.type == EVENT_ASSISTANT_MESSAGE
        for block in e.data.get("content", [])
        if isinstance(block, dict)
    ]
    assert "first" in texts
    assert "second" in texts


@pytest.mark.asyncio
async def test_resume_continues_turn_number(tmp_path):
    """接续已有事件流时 turn/start 应从上次最大 turn 继续，而不是从 1 重计。"""
    writer = LocalFileTranscriptWriter(tmp_path / "sid.jsonl")
    engine1 = create_test_engine(
        responses=[
            {"text": "t1", "tool_calls": []},
            {"text": "t2", "tool_calls": []},
        ],
        transcript_writer=writer,
        config_overrides={"session_id": "sid"},
    )
    async with engine1:
        await _collect(engine1)
        await _collect(engine1)

    engine2 = create_test_engine(
        responses=[{"text": "t3", "tool_calls": []}],
        transcript_writer=writer,
        config_overrides={"session_id": "sid"},
    )
    async with engine2:
        await _collect(engine2)

    events = await writer.read_events()
    turns = [e.data["turn"] for e in events if e.type == EVENT_TURN_START]
    assert turns == [1, 2, 3]
    ends = [e.data["turn"] for e in events if e.type == EVENT_TURN_END]
    assert ends == [1, 2, 3]
