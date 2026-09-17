"""事件目录全量实装的覆盖测试 —— 对照 docs/Session-Observability-Design.md §4.1。

每个 ``EVENT_*`` 常量都要有真实发射路径：本文件按事件类型逐个断言，
并用 ``KNOWN_EVENT_TYPES`` 兜底，防止新增常量后忘记接线。
"""

import pytest

from nono_agent.core.types import ToolResult
from nono_agent.memory.models import MemoryRecord
from nono_agent.observability.metrics import (
    METRIC_SESSION_EVENTS,
    InMemoryMetricsRecorder,
    set_metrics_recorder,
)
from nono_agent.session import (
    LocalFileTranscriptWriter,
    derive_messages,
    messages_to_derived,
    verify_reconstruction,
)
from nono_agent.session.events import (
    EVENT_API_RETRY,
    EVENT_ASSISTANT_CHUNK,
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_MEMORY_RETRIEVE,
    EVENT_MEMORY_WRITE,
    EVENT_PERMISSION_CHANGED,
    EVENT_PERMISSION_DENIAL,
    EVENT_PERMISSION_ONCHANGE,
    EVENT_REQUEST_HEADER,
    EVENT_STEP_END,
    EVENT_STEP_START,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USAGE,
    EVENT_USER_MESSAGE,
    KNOWN_EVENT_TYPES,
)
from nono_agent.tools.protocol import ToolBase
from tests.helpers import MockTool, create_test_engine


async def _collect(engine, text="hello"):
    return [msg async for msg in engine.submit_message(text)]


def _writer(tmp_path, name="s.jsonl"):
    return LocalFileTranscriptWriter(tmp_path / name)


class DenyingTool(ToolBase):
    """执行时记录一次权限拒绝的工具 —— 覆盖 permission/denial 发射路径。"""

    @property
    def name(self):
        return "denied_tool"

    @property
    def description(self):
        return "records a permission denial"

    @property
    def input_schema(self):
        return {"type": "object", "properties": {}}

    async def execute(self, input_params, context):
        context.denial_tracker.record(
            self.name, "path outside sandbox", {"path": "/etc/passwd"}, mode="default"
        )
        return ToolResult(tool_use_id="", content="denied", is_error=True,
                          tool_name=self.name)


@pytest.mark.asyncio
async def test_single_turn_emits_turn_step_request_and_usage(tmp_path):
    """一次无工具的问答就覆盖 turn/step/request-header/user/assistant/usage。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "hi", "tool_calls": []}], transcript_writer=writer
    )
    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    types = [e.type for e in events]
    for expected in (
        EVENT_TURN_START,
        EVENT_STEP_START,
        EVENT_REQUEST_HEADER,
        EVENT_USER_MESSAGE,
        EVENT_ASSISTANT_CHUNK,
        EVENT_ASSISTANT_MESSAGE,
        EVENT_USAGE,
        EVENT_STEP_END,
        EVENT_TURN_END,
    ):
        assert expected in types, f"{expected} not emitted"

    # step 成对出现，且编号从 1 开始递增
    starts = [e for e in events if e.type == EVENT_STEP_START]
    ends = [e for e in events if e.type == EVENT_STEP_END]
    assert len(starts) == len(ends)
    assert [e.data["step"] for e in starts] == list(range(1, len(starts) + 1))
    assert ends[-1].data["reason"] == "completed"


@pytest.mark.asyncio
async def test_step_numbering_spans_tool_iterations(tmp_path):
    """一次工具调用产生两个 step：请求→工具，和收尾请求。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "calc", "id": "t1", "input": {}}]},
            {"text": "done", "tool_calls": []},
        ],
        tools=[MockTool(name="calc", response="42")],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    ends = [e for e in events if e.type == EVENT_STEP_END]
    assert [e.data["step"] for e in ends] == [1, 2]
    assert [e.data["reason"] for e in ends] == ["next_turn", "completed"]
    assert EVENT_TOOL_CALL in [e.type for e in events]
    assert EVENT_TOOL_RESULT in [e.type for e in events]


@pytest.mark.asyncio
async def test_assistant_chunks_can_be_downgraded(tmp_path):
    """log_assistant_chunks=False 是降级模式：只留合并后的 assistant/message。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "hi", "tool_calls": []}],
        transcript_writer=writer,
        config_overrides={"log_assistant_chunks": False},
    )
    async with engine:
        await _collect(engine)

    types = [e.type for e in await writer.read_events()]
    assert EVENT_ASSISTANT_CHUNK not in types
    assert EVENT_ASSISTANT_MESSAGE in types


@pytest.mark.asyncio
async def test_permission_denial_event_from_tracker(tmp_path):
    """拒绝发生在工具内部，靠 DenialTracker 观察者进事件流。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[
            {
                "text": "",
                "tool_calls": [{"name": "denied_tool", "id": "t1", "input": {}}],
            },
            {"text": "ok", "tool_calls": []},
        ],
        tools=[DenyingTool()],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine)

    denials = [
        e for e in await writer.read_events() if e.type == EVENT_PERMISSION_DENIAL
    ]
    assert len(denials) == 1
    assert denials[0].data["tool"] == "denied_tool"
    assert denials[0].data["path"] == "/etc/passwd"


@pytest.mark.asyncio
async def test_permission_onchange_then_changed_at_next_turn(tmp_path):
    """回合之间切权限：立刻 onchange，下一次 turn/start 之前 changed。"""
    from nono_agent.permissions import PermissionMode

    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[
            {"text": "first", "tool_calls": []},
            {"text": "second", "tool_calls": []},
        ],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine, "round one")
        engine.permission_policy.request_mode(PermissionMode.PLAN)
        assert engine.permission_policy.mode is PermissionMode.DEFAULT
        await _collect(engine, "round two")
        assert engine.permission_policy.mode is PermissionMode.PLAN

    events = await writer.read_events()
    onchange = [e for e in events if e.type == EVENT_PERMISSION_ONCHANGE]
    changed = [e for e in events if e.type == EVENT_PERMISSION_CHANGED]
    assert len(onchange) == 1
    assert onchange[0].ignorable is True
    assert onchange[0].data == {"from": "default", "to": "plan"}
    assert len(changed) == 1
    assert changed[0].ignorable is True
    assert changed[0].data == {"from": "default", "to": "plan", "at": "turn"}

    types = [e.type for e in events]
    onchange_i = types.index(EVENT_PERMISSION_ONCHANGE)
    changed_i = types.index(EVENT_PERMISSION_CHANGED)
    turn_starts = [i for i, t in enumerate(types) if t == EVENT_TURN_START]
    assert onchange_i < changed_i
    assert changed_i == turn_starts[1] - 1


@pytest.mark.asyncio
async def test_permission_changed_at_next_step_after_plan_tool(tmp_path):
    """回合内 plan 工具：onchange 夹在工具执行中，下一 step 生效。"""
    from nono_agent.tools.builtin import EnterPlanModeTool

    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[
            {
                "text": "",
                "tool_calls": [{"name": "enter_plan_mode", "id": "t1", "input": {}}],
            },
            {"text": "ok", "tool_calls": []},
        ],
        tools=[EnterPlanModeTool()],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    types = [e.type for e in events]
    assert EVENT_PERMISSION_ONCHANGE in types
    assert EVENT_PERMISSION_CHANGED in types
    onchange_i = types.index(EVENT_PERMISSION_ONCHANGE)
    changed_i = types.index(EVENT_PERMISSION_CHANGED)
    step_starts = [i for i, t in enumerate(types) if t == EVENT_STEP_START]
    assert onchange_i > step_starts[0]
    assert changed_i == step_starts[1] - 1
    changed = next(e for e in events if e.type == EVENT_PERMISSION_CHANGED)
    assert changed.data["at"] == "step"
    assert changed.data["to"] == "plan"


@pytest.mark.asyncio
async def test_memory_write_and_retrieve_events(tmp_path):
    """记忆读写靠 MemoryManager 观察者进事件流。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "hi", "tool_calls": []}], transcript_writer=writer
    )
    async with engine:
        engine.memory_manager.admit(
            MemoryRecord(content="user prefers dark mode", importance=8.0)
        )
        engine.memory_manager.retrieve("dark mode")
        await _collect(engine)

    events = await writer.read_events()
    writes = [e for e in events if e.type == EVENT_MEMORY_WRITE]
    retrieves = [e for e in events if e.type == EVENT_MEMORY_RETRIEVE]
    assert len(writes) == 1
    assert writes[0].data["adopted"] is True
    assert writes[0].data["memory_id"]

    # 显式检索 + 系统提示组装时的自动检索都会留痕
    queries = [e.data["query"] for e in retrieves]
    assert "dark mode" in queries
    explicit = next(e for e in retrieves if e.data["query"] == "dark mode")
    assert explicit.data["memory_ids"] == [writes[0].data["memory_id"]]


@pytest.mark.asyncio
async def test_api_retry_event_on_model_fallback(tmp_path):
    """fallback 切换写 api/retry，并收束当前 step。"""
    from nono_agent.providers.base import FallbackTriggeredError

    class FailingOnceProvider:
        """首次调用触发 fallback，其后正常返回。"""

        def __init__(self):
            self.calls = 0

        name = "failing"
        supports_thinking = False
        supports_tool_choice = True

        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise FallbackTriggeredError(
                    original_model="claude-test", fallback_model="claude-backup"
                )
            from nono_agent.core.types import AssistantMessage

            yield AssistantMessage(
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "recovered"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "stop_reason": "end_turn",
                }
            )

        def count_tokens(self, messages, system_prompt, tools):
            return 10

        def normalize_messages(self, messages):
            return [m.message for m in messages if hasattr(m, "message")]

    writer = _writer(tmp_path)
    engine = create_test_engine(
        transcript_writer=writer,
        provider=FailingOnceProvider(),
        config_overrides={"fallback_model": "claude-backup"},
    )
    async with engine:
        await _collect(engine)

    events = await writer.read_events()
    retries = [e for e in events if e.type == EVENT_API_RETRY]
    assert len(retries) == 1
    assert retries[0].data["reason"] == "fallback_model"
    assert retries[0].data["attempt"] == 1
    assert retries[0].data["max_retries"] == 1
    assert retries[0].data["to_model"] == "claude-backup"

    # fallback 的那一 step 也被收束
    ends = [e for e in events if e.type == EVENT_STEP_END]
    assert ends[0].data["reason"] == "fallback_model"


class _ForceCompactStage:
    """测试用 stage：第二轮起把会话窗口整体替换，触发管线侧 compact/boundary。"""

    @property
    def name(self) -> str:
        return "force_compact"

    @property
    def priority(self) -> int:
        return 0

    def should_run(self, messages, context) -> bool:
        # 用户消息在管线压缩之后才追加进 store，因此第二轮管线看到的是
        # [user, assistant]（2 条）—— 阈值按「压缩前的历史」设定。
        return len(messages) >= 2

    async def execute(self, messages, context):
        from nono_agent.core.types import SystemMessage

        boundary = SystemMessage(
            subtype="compact_boundary",
            content="[Context compacted by preprocess pipeline.]",
            compact_metadata={"strategy": "force"},
        )
        context.metadata["compact_boundary_created"] = True
        return [boundary, messages[-1]], context


@pytest.mark.asyncio
async def test_pipeline_compact_emits_boundary_and_keeps_invariant(tmp_path):
    """管线 ``store.replace()`` 必须写 compact/boundary，否则下一请求会被门禁拦住。"""
    from nono_agent.pipeline.base import PreprocessPipeline

    pipeline = PreprocessPipeline()
    pipeline.register(_ForceCompactStage())
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "first", "tool_calls": []}, {"text": "second", "tool_calls": []}],
        transcript_writer=writer,
        pipeline=pipeline,
        config_overrides={"session_invariant": "strict"},
    )
    async with engine:
        await _collect(engine, "round one")
        result = [m async for m in engine.submit_message("round two")]

    assert next(m for m in result if getattr(m, "type", "") == "result").is_error is False
    events = await writer.read_events()
    boundaries = [e for e in events if e.type == EVENT_COMPACT_BOUNDARY]
    assert len(boundaries) == 1
    assert boundaries[0].data["metadata"]["strategy"] == "pipeline"
    assert boundaries[0].data["kept"]
    verify_reconstruction(events, messages_to_derived(engine.message_store.all_messages()))


@pytest.mark.asyncio
async def test_engine_projects_events_onto_metrics(tmp_path):
    """``_stage_event`` 是指标投影的唯一入口 —— 真实回合必须留下 session.events。"""
    recorder = InMemoryMetricsRecorder()
    set_metrics_recorder(recorder)
    try:
        writer = _writer(tmp_path)
        engine = create_test_engine(
            responses=[{"text": "hi", "tool_calls": []}], transcript_writer=writer
        )
        async with engine:
            await _collect(engine)
        counters = recorder.snapshot()["counters"]
        assert any(
            k.startswith(METRIC_SESSION_EVENTS) and "event=turn/start" in k
            for k in counters
        )
        assert any(
            k.startswith(METRIC_SESSION_EVENTS) and "event=usage" in k
            for k in counters
        )
    finally:
        set_metrics_recorder(InMemoryMetricsRecorder())


@pytest.mark.asyncio
async def test_clear_session_logs_boundary(tmp_path):
    """/clear 是状态替换 —— 必须写 kept=[] 的 compact/boundary。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[{"text": "a", "tool_calls": []}, {"text": "b", "tool_calls": []}],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine)
        engine.clear_session()
        await _collect(engine, "after clear")

    events = await writer.read_events()
    boundaries = [e for e in events if e.type == EVENT_COMPACT_BOUNDARY]
    assert len(boundaries) == 1
    assert boundaries[0].data["kept"] == []
    assert boundaries[0].data["metadata"]["strategy"] == "clear"

    # 重建结果只剩清空之后的那一轮，第一轮的历史彻底消失
    derived = derive_messages(events)
    assert derived == [
        {"role": "user", "content": "after clear"},
        {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
    ]


@pytest.mark.asyncio
async def test_every_known_event_type_has_an_emitter():
    """KNOWN_EVENT_TYPES 中的每个类型都在产品代码里被发射。

    纯静态扫描：常量新增而没有接线时，这里会失败。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "nono_agent"
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.py")
    )
    emitted = {
        name
        for name in dir(__import__("nono_agent.session.events", fromlist=["x"]))
        if name.startswith("EVENT_")
    }
    for const in sorted(emitted):
        # 常量定义处 + 至少一个发射点（_stage_event/emit 调用）
        assert sources.count(const) >= 2, f"{const} 只有定义，没有发射点"
    assert len(KNOWN_EVENT_TYPES) == len(emitted)


@pytest.mark.asyncio
async def test_log_reconstructs_state_across_tool_turns(tmp_path):
    """端到端不变量：事件流重建 == 运行时消息状态。"""
    writer = _writer(tmp_path)
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "calc", "id": "t1", "input": {}}]},
            {"text": "done", "tool_calls": []},
        ],
        tools=[MockTool(name="calc", response="42")],
        transcript_writer=writer,
    )
    async with engine:
        await _collect(engine)
        state = messages_to_derived(engine.message_store.all_messages())

    verify_reconstruction(await writer.read_events(), state)
