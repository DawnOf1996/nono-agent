"""事件顺序调整后的回归测试。

覆盖两处整改：
1. ``user/input`` 新增事件：紧跟 ``turn/start``、先于 ``step/start``，且不参与 derive。
2. ``user/message`` 移到 ``step/start`` 之后、``request/header`` 之前（模型调用请求开始时
   最终要发送的、组装好的用户信息）。
3. ``request/header`` 的 ``config`` 携带模型提供商信息 / 协议 / 上下文大小。
"""

import pytest

from nono_agent.session import LocalFileTranscriptWriter, derive_messages
from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_REQUEST_HEADER,
    EVENT_STEP_START,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USAGE,
    EVENT_USER_INPUT,
    EVENT_USER_MESSAGE,
    KNOWN_EVENT_TYPES,
    SessionEvent,
)
from tests.helpers import MockTool, create_test_engine


async def _collect(engine, text="hello"):
    return [msg async for msg in engine.submit_message(text)]


def _writer(tmp_path, name="s.jsonl"):
    return LocalFileTranscriptWriter(tmp_path / name)


def _index_of(events, type_):
    return next(i for i, e in enumerate(events) if e.type == type_)


@pytest.mark.asyncio
async def test_user_input_precedes_step_and_user_message_under_step(tmp_path):
    """单轮无工具：turn/start → user/input → step/start → user/message → request/header。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "hi", "tool_calls": []}], transcript_writer=writer
    )
    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    types = [e.type for e in events]

    assert EVENT_USER_INPUT in types
    assert _index_of(events, EVENT_TURN_START) < _index_of(events, EVENT_USER_INPUT)
    assert _index_of(events, EVENT_USER_INPUT) < _index_of(events, EVENT_STEP_START)
    assert _index_of(events, EVENT_STEP_START) < _index_of(events, EVENT_USER_MESSAGE)
    assert _index_of(events, EVENT_USER_MESSAGE) < _index_of(events, EVENT_REQUEST_HEADER)
    assert _index_of(events, EVENT_REQUEST_HEADER) < _index_of(events, EVENT_ASSISTANT_MESSAGE)

    # user/input 载荷是原始输入
    user_input = events[_index_of(events, EVENT_USER_INPUT)]
    assert user_input.data["content"] == "hello"


@pytest.mark.asyncio
async def test_user_input_is_not_folded_into_derive(tmp_path):
    """user/input 是观测资产，derive 只看 user/message（组装后的最终消息）。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "hi", "tool_calls": []}], transcript_writer=writer
    )
    async with engine:
        await _collect(engine, "raw input")

    events = await writer.read_events()
    derived = derive_messages(events)
    assert derived == [
        {"role": "user", "content": "raw input"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]


@pytest.mark.asyncio
async def test_user_input_is_a_known_event_type():
    assert EVENT_USER_INPUT in KNOWN_EVENT_TYPES


@pytest.mark.asyncio
async def test_request_header_config_carries_provider_protocol_and_context_size(tmp_path):
    """request/header 的 config 需包含提供商 / 协议 / 上下文大小，便于回溯。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "hi", "tool_calls": []}], transcript_writer=writer
    )
    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    header = next(e for e in events if e.type == EVENT_REQUEST_HEADER)
    config = header.data["header"]["config"]
    assert config["model"] == "claude-test"
    assert config["provider"] == "mock"  # MockProvider.name
    assert config["protocol"] == "mock"  # 无显式 adapter 时回退到 provider.name
    assert config["context_size"] > 0


@pytest.mark.asyncio
async def test_tool_turn_order_keeps_usage_before_step_end(tmp_path):
    """带工具：assistant/message → tool/call → tool/result 都落在 step/end 之前。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "calc", "id": "t1", "input": {}}]},
            {"text": "42", "tool_calls": []},
        ],
        tools=[MockTool(name="calc", response="42")],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine, "compute")

    events = await writer.read_events()
    assistant = _index_of(events, EVENT_ASSISTANT_MESSAGE)
    tool_call = _index_of(events, EVENT_TOOL_CALL)
    tool_result = _index_of(events, EVENT_TOOL_RESULT)
    usage = _index_of(events, EVENT_USAGE)
    step_end = next(i for i, e in enumerate(events) if e.type == "step/end")
    turn_end = _index_of(events, EVENT_TURN_END)

    assert assistant < tool_call < tool_result < step_end
    assert usage < step_end
    assert step_end < turn_end


def test_user_input_event_serializes_ignorable_default():
    """新增事件类型的默认 ignorable 为 False（属于本版本认识的必读事件）。"""
    ev = SessionEvent.make(1, EVENT_USER_INPUT, content="hi")
    assert ev.ignorable is False
