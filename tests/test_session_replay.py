"""回放 —— 不调用 LLM，从事件流重建当时的输出流。"""

import pytest

from nono_agent.session import (
    LocalFileTranscriptWriter,
    replay_sdk_messages,
    replay_transcript_text,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_TURN_END,
    EVENT_USAGE,
    EVENT_USER_MESSAGE,
    SessionEvent,
)
from tests.helpers import MockTool, create_test_engine


def _events(*specs):
    return [SessionEvent.make(i + 1, t, **d) for i, (t, d) in enumerate(specs)]


class TestReplaySdkMessages:
    def test_replays_conversation_in_order(self):
        events = _events(
            (EVENT_USER_MESSAGE, {"content": "hi"}),
            (EVENT_ASSISTANT_MESSAGE, {"content": [{"type": "text", "text": "yo"}]}),
            (EVENT_TURN_END, {"turn": 1, "reason": "COMPLETED"}),
        )
        replayed = list(replay_sdk_messages(events, session_id="s1"))
        assert [m.type for m in replayed] == ["user", "assistant", "result"]
        assert all(m.session_id == "s1" for m in replayed)

    def test_result_carries_accumulated_usage(self):
        events = _events(
            (EVENT_USER_MESSAGE, {"content": "hi"}),
            (EVENT_USAGE, {"usage": {"input_tokens": 10, "output_tokens": 5}, "model": "m"}),
            (EVENT_USAGE, {"usage": {"input_tokens": 3, "output_tokens": 2}, "model": "m"}),
            (EVENT_TURN_END, {"turn": 1, "reason": "COMPLETED"}),
        )
        result = list(replay_sdk_messages(events))[-1]
        assert result.usage.input_tokens == 13
        assert result.usage.output_tokens == 7

    def test_error_turn_becomes_error_result(self):
        events = _events((EVENT_TURN_END, {"turn": 1, "reason": "MODEL_ERROR"}))
        result = list(replay_sdk_messages(events))[0]
        assert result.is_error is True
        assert result.subtype == "error_model_error"

    def test_compact_boundary_becomes_system_message(self):
        events = _events(
            (
                EVENT_COMPACT_BOUNDARY,
                {
                    "content": "[compacted]",
                    "metadata": {"strategy": "manual"},
                    "kept": [],
                },
            )
        )
        msg = list(replay_sdk_messages(events))[0]
        assert msg.subtype == "compact_boundary"
        assert msg.compact_metadata == {"strategy": "manual"}

    def test_chunks_only_replayed_when_requested(self):
        from nono_agent.session.events import EVENT_ASSISTANT_CHUNK

        events = _events(
            (
                EVENT_ASSISTANT_CHUNK,
                {"chunk": {"type": "content_block_delta", "index": 0,
                           "delta": {"type": "text_delta", "text": "y"}}},
            )
        )
        assert list(replay_sdk_messages(events)) == []
        streamed = list(replay_sdk_messages(events, include_chunks=True))
        assert [m.type for m in streamed] == ["stream_event"]

    def test_events_need_no_pre_sorting(self):
        events = [
            SessionEvent.make(2, EVENT_ASSISTANT_MESSAGE, content=[]),
            SessionEvent.make(1, EVENT_USER_MESSAGE, content="hi"),
        ]
        assert [m.type for m in replay_sdk_messages(events)] == ["user", "assistant"]


class TestReplayTranscriptText:
    def test_renders_readable_transcript(self):
        events = _events(
            (EVENT_USER_MESSAGE, {"content": "what is 6*7"}),
            (EVENT_ASSISTANT_MESSAGE, {"content": [{"type": "text", "text": "42"}]}),
        )
        assert replay_transcript_text(events) == "user: what is 6*7\nassistant: 42"

    def test_ignores_non_text_blocks(self):
        events = _events(
            (
                EVENT_ASSISTANT_MESSAGE,
                {"content": [{"type": "tool_use", "id": "t", "name": "calc"}]},
            )
        )
        assert replay_transcript_text(events) == "assistant: "


@pytest.mark.asyncio
async def test_replays_a_real_run_without_calling_the_model(tmp_path):
    """端到端：跑一轮带工具的会话，再纯从事件流重放出同样的输出序列。"""
    writer = LocalFileTranscriptWriter(tmp_path / "s.jsonl")
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "calc", "id": "t1", "input": {}}]},
            {"text": "the answer is 42", "tool_calls": []},
        ],
        tools=[MockTool(name="calc", response="42")],
        transcript_writer=writer,
    )
    async with engine:
        live = [m async for m in engine.submit_message("compute")]

    events = await writer.read_events()
    replayed = list(replay_sdk_messages(events, session_id=engine.session_id))

    live_texts = [
        block["text"]
        for m in live
        if m.type == "assistant"
        for block in m.message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    replay_texts = [
        block["text"]
        for m in replayed
        if m.type == "assistant"
        for block in m.message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    assert replay_texts == live_texts
    assert "the answer is 42" in replay_transcript_text(events)
