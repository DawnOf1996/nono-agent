"""请求前一致性门禁 —— 事件流必须能重建出运行时消息状态。

门禁的价值在于「绕过事件流的状态变更会被抓住」，所以这里既验证正常路径放行，
也人为制造漏记场景验证它确实拦得住。
"""

import pytest

from nono_agent.adapters.testing import MockTool, create_test_engine
from nono_agent.core.types import UserMessage
from nono_agent.observability.metrics import (
    METRIC_SESSION_DESYNC,
    InMemoryMetricsRecorder,
    set_metrics_recorder,
)
from nono_agent.session import LocalFileTranscriptWriter, ReconstructionError
from nono_agent.session.derive import messages_to_derived, verify_reconstruction


async def _collect(engine, text="hello"):
    return [msg async for msg in engine.submit_message(text)]


def _result(messages):
    return next(m for m in messages if getattr(m, "type", "") == "result")


@pytest.mark.asyncio
async def test_strict_mode_passes_on_clean_session(tmp_path):
    writer = LocalFileTranscriptWriter(tmp_path / "s.jsonl")
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "calc", "id": "t1", "input": {}}]},
            {"text": "done", "tool_calls": []},
        ],
        tools=[MockTool(name="calc", response="42")],
        transcript_writer=writer,
        config_overrides={"session_invariant": "strict"},
    )
    async with engine:
        result = _result(await _collect(engine))

    assert result.is_error is False
    verify_reconstruction(
        await writer.read_events(),
        messages_to_derived(engine.message_store.all_messages()),
    )


@pytest.mark.asyncio
async def test_strict_mode_aborts_turn_on_unlogged_state_change(tmp_path):
    """直接改 store 而不写事件 —— 下一次请求必须被门禁拦住。"""
    writer = LocalFileTranscriptWriter(tmp_path / "s.jsonl")
    engine = create_test_engine(
        responses=[{"text": "a", "tool_calls": []}, {"text": "b", "tool_calls": []}],
        transcript_writer=writer,
        config_overrides={"session_invariant": "strict"},
    )
    async with engine:
        await _collect(engine)
        # 模拟「有人偷偷改了会话状态」：只动内存，不写事件流
        engine.message_store.append(
            UserMessage(message={"role": "user", "content": "smuggled"})
        )
        result = _result(await _collect(engine, "next"))

    assert result.is_error is True
    assert result.subtype == "error_log_reconstruction_desync"
    assert any("smuggled" in err for err in result.errors)


@pytest.mark.asyncio
async def test_warn_mode_records_metric_but_completes(tmp_path):
    recorder = InMemoryMetricsRecorder()
    set_metrics_recorder(recorder)

    writer = LocalFileTranscriptWriter(tmp_path / "s.jsonl")
    engine = create_test_engine(
        responses=[{"text": "a", "tool_calls": []}, {"text": "b", "tool_calls": []}],
        transcript_writer=writer,
        config_overrides={"session_invariant": "warn"},
    )
    async with engine:
        await _collect(engine)
        engine.message_store.append(
            UserMessage(message={"role": "user", "content": "smuggled"})
        )
        result = _result(await _collect(engine, "next"))

    assert result.is_error is False
    counters = recorder.snapshot()["counters"]
    assert any(key.startswith(METRIC_SESSION_DESYNC) for key in counters)


@pytest.mark.asyncio
async def test_off_mode_skips_the_check(tmp_path):
    writer = LocalFileTranscriptWriter(tmp_path / "s.jsonl")
    engine = create_test_engine(
        responses=[{"text": "a", "tool_calls": []}, {"text": "b", "tool_calls": []}],
        transcript_writer=writer,
        config_overrides={"session_invariant": "off"},
    )
    async with engine:
        await _collect(engine)
        engine.message_store.append(
            UserMessage(message={"role": "user", "content": "smuggled"})
        )
        result = _result(await _collect(engine, "next"))

    assert result.is_error is False


@pytest.mark.asyncio
async def test_pipeline_cannot_rewrite_recorded_history(tmp_path):
    """管线拿到的是深拷贝：就地截断工具结果不会改写已记录的状态。"""
    writer = LocalFileTranscriptWriter(tmp_path / "s.jsonl")
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "big", "id": "t1", "input": {}}]},
            {"text": "done", "tool_calls": []},
        ],
        tools=[MockTool(name="big", response="X" * 200_000)],
        transcript_writer=writer,
        config_overrides={"session_invariant": "strict"},
    )
    async with engine:
        result = _result(await _collect(engine))

    # 管线一定截断了发给模型的副本，但 store 里的原始结果保持完整
    assert result.is_error is False
    stored = [
        block["content"]
        for msg in engine.message_store.all_messages()
        if isinstance(msg, UserMessage)
        for block in (msg.message.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert stored and len(stored[0]) == 200_000


@pytest.mark.asyncio
async def test_engine_hydrates_state_from_existing_log(tmp_path):
    """指向已有事件流即接续该会话 —— 否则会拿空上下文继续请求。"""
    session_file = tmp_path / "s.jsonl"
    first = create_test_engine(
        responses=[{"text": "first", "tool_calls": []}],
        transcript_writer=LocalFileTranscriptWriter(session_file),
    )
    async with first:
        await _collect(first)

    second = create_test_engine(
        responses=[{"text": "second", "tool_calls": []}],
        transcript_writer=LocalFileTranscriptWriter(session_file),
    )
    async with second:
        assert len(second.message_store.all_messages()) == 2  # 重建出第一轮
        result = _result(await _collect(second, "again"))

    assert result.is_error is False


@pytest.mark.asyncio
async def test_verify_reconstruction_points_at_first_difference():
    from nono_agent.session.events import EVENT_USER_MESSAGE, SessionEvent

    events = [SessionEvent.make(1, EVENT_USER_MESSAGE, content="logged")]
    with pytest.raises(ReconstructionError) as excinfo:
        verify_reconstruction(events, [{"role": "user", "content": "actual"}])
    assert "messages[0] differs" in str(excinfo.value)
    assert "actual" in str(excinfo.value)
