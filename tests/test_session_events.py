"""Tests for session event sourcing: SessionEvent / derive / request-header."""

import pytest

from nono_agent.core.types import AssistantMessage, SystemMessage, UserMessage
from nono_agent.session import (
    ReconstructionError,
    SessionEvent,
    canonical_header,
    derive_messages,
    derived_to_messages,
    fold_request_header,
    header_equals,
    message_to_derived,
    messages_to_derived,
    next_reason,
    verify_reconstruction,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_UI_TOOL_RESULT,
    EVENT_USER_MESSAGE,
    last_turn_number,
)


def _ev(seq, type_, **data):
    return SessionEvent.make(seq, type_, **data)


class TestSessionEvent:
    def test_make_and_serialize(self):
        ev = _ev(3, EVENT_USER_MESSAGE, content=[{"type": "text", "text": "hi"}])
        assert ev.seq == 3
        assert ev.type == EVENT_USER_MESSAGE
        assert ev.ignorable is False
        dumped = ev.model_dump()
        assert dumped["seq"] == 3
        assert dumped["data"]["content"][0]["text"] == "hi"
        assert "timestamp" in dumped

    def test_last_turn_number_from_start_and_end(self):
        events = [
            _ev(1, EVENT_TURN_START, turn=1),
            _ev(2, EVENT_TURN_END, turn=1),
            _ev(3, EVENT_TURN_START, turn=2),
        ]
        assert last_turn_number(events) == 2
        assert last_turn_number([]) == 0

    def test_ignorable_flag(self):
        ev = _ev(1, "future/type", ignorable=True)
        assert ev.ignorable is True

    def test_ui_tool_result_event_stays_out_of_model_context(self):
        """UI 附件事件是 ignorable 的：不折叠进消息，也就永远不进模型上下文。

        它落在会话日志里（重开历史能还原差异卡片），但对 derive 是隐形的 ——
        这正是"给界面的数据不喂给模型"这条约束的落点。
        """
        ui = {
            "kind": "file_diff",
            "path": "a.py",
            "added": 1,
            "removed": 0,
            "diff": "@@ -1 +1 @@",
        }
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="改一下"),
            _ev(
                2,
                EVENT_ASSISTANT_MESSAGE,
                content=[{"type": "tool_use", "id": "t1", "name": "edit_file"}],
            ),
            _ev(
                3,
                EVENT_UI_TOOL_RESULT,
                ignorable=True,
                call_id="t1",
                tool_name="edit_file",
                ui=ui,
            ),
            _ev(4, EVENT_TOOL_RESULT, call_id="t1", result="ok", is_error=False),
        ]
        derived = derive_messages(events)
        assert [m["role"] for m in derived] == ["user", "assistant", "user"]
        assert "file_diff" not in repr(derived)
        # 事件本身还在：重开历史会话时前端照样拿得到差异。
        assert [e for e in events if e.type == EVENT_UI_TOOL_RESULT][0].data["ui"] == ui


class TestDeriveMessages:
    def test_folds_user_assistant(self):
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="hello"),
            _ev(2, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "hi there"}]),
        ]
        assert derive_messages(events) == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
        ]

    def test_folds_tool_result(self):
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="run ls"),
            _ev(2, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "ok"}]),
            _ev(3, EVENT_TOOL_RESULT, call_id="c-1", result="README.md", is_error=False),
        ]
        messages = derive_messages(events)
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"][0]["type"] == "tool_result"
        assert messages[-1]["content"][0]["tool_use_id"] == "c-1"

    def test_ignores_structural_events(self):
        events = [
            _ev(1, EVENT_TURN_START, turn=1),
            _ev(2, EVENT_USER_MESSAGE, content="hi"),
            _ev(3, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "yo"}]),
            _ev(4, EVENT_TURN_END, turn=1),
        ]
        assert len(derive_messages(events)) == 2

    def test_sorts_by_seq(self):
        events = [
            _ev(2, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "second"}]),
            _ev(1, EVENT_USER_MESSAGE, content="first"),
        ]
        assert [m["role"] for m in derive_messages(events)] == ["user", "assistant"]

    def test_compact_boundary_replaces_history_with_kept_window(self):
        """kept 是压缩后的完整窗口 —— 折叠即整体替换，不再隐式补边界标记。"""
        boundary_marker = {
            "role": "system",
            "subtype": "compact_boundary",
            "content": "[compacted]",
        }
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="ancient"),
            _ev(2, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "old"}]),
            _ev(
                3,
                EVENT_COMPACT_BOUNDARY,
                content="[compacted]",
                kept=[boundary_marker, {"role": "user", "content": "recent"}],
            ),
            _ev(4, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "new"}]),
        ]
        assert derive_messages(events) == [
            boundary_marker,
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": [{"type": "text", "text": "new"}]},
        ]

    def test_compact_boundary_without_kept_clears_history(self):
        """/clear 写 kept=[] —— 折叠结果就是空上下文。"""
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="ancient"),
            _ev(2, EVENT_COMPACT_BOUNDARY, content="[Session cleared.]", kept=[]),
        ]
        assert derive_messages(events) == []

    def test_parallel_tool_results_coalesce_into_one_message(self):
        """并行工具的结果必须落在同一条 user 消息里（API 协议要求）。"""
        events = [
            _ev(1, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "go"}]),
            _ev(2, EVENT_TOOL_RESULT, call_id="a", result="ra", is_error=False),
            _ev(3, EVENT_TOOL_RESULT, call_id="b", result="rb", is_error=False),
        ]
        derived = derive_messages(events)
        assert [m["role"] for m in derived] == ["assistant", "user"]
        assert [b["tool_use_id"] for b in derived[1]["content"]] == ["a", "b"]

    def test_tool_results_split_by_intervening_assistant_message(self):
        """被 assistant 消息隔开的工具结果属于不同轮，不能并入同一条消息。"""
        events = [
            _ev(1, EVENT_TOOL_RESULT, call_id="a", result="ra", is_error=False),
            _ev(2, EVENT_ASSISTANT_MESSAGE, content=[{"type": "text", "text": "mid"}]),
            _ev(3, EVENT_TOOL_RESULT, call_id="b", result="rb", is_error=False),
        ]
        assert [m["role"] for m in derive_messages(events)] == [
            "user",
            "assistant",
            "user",
        ]

    def test_unknown_ignorable_event_skipped(self):
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="hi"),
            _ev(2, "future/type", ignorable=True, whatever=1),
        ]
        assert len(derive_messages(events)) == 1

    def test_unknown_required_event_rejected(self):
        events = [
            _ev(1, EVENT_USER_MESSAGE, content="hi"),
            _ev(2, "future/type", whatever=1),
        ]
        with pytest.raises(ReconstructionError):
            derive_messages(events)

    def test_verify_passes_when_match(self):
        events = [_ev(1, EVENT_USER_MESSAGE, content="hi")]
        verify_reconstruction(events, [{"role": "user", "content": "hi"}])

    def test_verify_raises_on_desync(self):
        events = [_ev(1, EVENT_USER_MESSAGE, content="hi")]
        with pytest.raises(ReconstructionError):
            verify_reconstruction(events, [{"role": "user", "content": "bye"}])


class TestMessageConversion:
    def test_roundtrip_is_identity(self):
        messages = [
            UserMessage(message={"role": "user", "content": "hi"}),
            AssistantMessage(
                message={"role": "assistant", "content": [{"type": "text", "text": "yo"}]}
            ),
            SystemMessage(subtype="compact_boundary", content="[compacted]"),
        ]
        derived = messages_to_derived(messages)
        assert messages_to_derived(derived_to_messages(derived)) == derived

    def test_non_context_messages_dropped(self):
        from nono_agent.core.types import ProgressMessage

        progress = ProgressMessage(content="running")
        assert message_to_derived(progress) is None
        assert messages_to_derived([progress]) == []


class TestRequestHeader:
    def test_canonical_normalizes(self):
        h = canonical_header(config={"b": 1, "a": 2}, system="s", tools=[])
        assert h["config"] == {"a": 2, "b": 1}
        assert h["system"] == "s"
        assert h["tools"] == []

    def test_header_equals_same(self):
        a = canonical_header(config={"model": "x"}, system="s", tools=[{"n": "t"}])
        b = canonical_header(config={"model": "x"}, system="s", tools=[{"n": "t"}])
        assert header_equals(a, b)

    def test_header_equals_differs_on_system(self):
        assert not header_equals(canonical_header(system="A"), canonical_header(system="B"))

    def test_header_equals_differs_on_tools_order(self):
        a = canonical_header(tools=[{"n": "a"}, {"n": "b"}])
        b = canonical_header(tools=[{"n": "b"}, {"n": "a"}])
        assert not header_equals(a, b)

    def test_next_reason_initial(self):
        assert next_reason(None, {"system": "s"}, instance_logged=False) == "initial"

    def test_next_reason_resume(self):
        assert next_reason({"system": "old"}, {"system": "new"}, instance_logged=False) == "resume"

    def test_next_reason_change(self):
        assert next_reason({"system": "old"}, {"system": "new"}, instance_logged=True) == "change"

    def test_next_reason_no_change(self):
        assert next_reason({"system": "s"}, {"system": "s"}, instance_logged=True) is None

    def test_fold_returns_latest_header(self):
        events = [
            _ev(1, EVENT_REQUEST_HEADER, header={"system": "first"}, reason="initial"),
            _ev(2, EVENT_USER_MESSAGE, content="hi"),
            _ev(3, EVENT_REQUEST_HEADER, header={"system": "second"}, reason="change"),
        ]
        assert fold_request_header(events) == {"system": "second"}

    def test_fold_returns_none_without_header(self):
        assert fold_request_header([_ev(1, EVENT_USER_MESSAGE, content="hi")]) is None
