"""用户中止（停止按钮）的行为契约。

两条主线：

1. **流式期间按停止要立刻生效**。真实 provider 在"等下一个 chunk"时是纯网络阻塞，
   占墙钟时间最长；以前那里没人读 ``signal.aborted``，信号置位了也没人看，
   于是表现为"点了停止毫无反应"——实际要等模型把整段话说完。
   MockProvider 瞬时吐完，测不出这个。

2. **中止必须 session 独享**。停 A 不能波及 B —— 引擎、取消控制器、provider 实例
   都是按会话建的，这条要钉死，防止以后有人图省事把它做成全局信号。

⚠️ 写这类测试时踩过的坑：**测试用的 provider 绝不能自己检查 signal**。
第一版让 SlowStreamProvider 内部 ``if signal.aborted: return``，结果把 loop.py 的
修复整个去掉，9 条测试照样全绿 —— 它测到的是 provider 自己那道守卫，不是循环层
的竞速。下面的 provider 一律不看 signal，唤醒的责任只在循环层。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from nono_agent.core.types import (
    AssistantMessage,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    MessageDeltaEvent,
    MessageStartEvent,
    SDKResultMessage,
)
from nono_agent.utils.abort import AbortController, iterate_with_abort
from tests.helpers import create_test_engine


class StallingProvider:
    """吐 ``chunks`` 个字后长时间不出字的 provider。

    刻意**不检查 signal**：真实 provider 阻塞在"等下一个 chunk"时没人能读信号，
    唤醒的责任在循环层。

    卡住不出字对应两种真实场景，也正是用户会按停止的时刻：

    - 首 token 之前（长 thinking / 长 TTFT）
    - 流中途 provider 迟迟不推数据

    Attributes:
        hold: 吐完 chunk 后空转多久再收尾。None = 立刻正常结束。
            设成远大于测试阈值的数，用于验证"中止能不能打断这段空转"。
        completed: 是否不被打断地走完（被中止则 False）。
        closed: 生成器是否已收尾（连接释放的证据）。
        calls: 被请求了几次 —— 中止后不该有第二次。
    """

    def __init__(
        self,
        chunks: int = 3,
        chunk_delay: float = 0.02,
        hold: float | None = None,
        tool_use: bool = False,
    ) -> None:
        self.chunks = chunks
        self.chunk_delay = chunk_delay
        self.hold = hold
        self.tool_use = tool_use
        self.calls = 0
        self.completed = False
        self.closed = False
        self.chunks_yielded = 0

    @property
    def name(self) -> str:
        return "stalling"

    @property
    def supports_thinking(self) -> bool:
        return False

    @property
    def supports_tool_choice(self) -> bool:
        return True

    async def stream(
        self,
        *,
        messages: list[Any],
        system_prompt: str,
        tools: list[dict[str, Any]],
        signal: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.calls += 1
        try:
            yield MessageStartEvent(
                message={"usage": {"input_tokens": 10, "output_tokens": 0}}
            )
            yield ContentBlockStartEvent(
                index=0, content_block={"type": "text", "text": ""}
            )
            for _ in range(self.chunks):
                await asyncio.sleep(self.chunk_delay)
                yield ContentBlockDeltaEvent(
                    index=0, delta={"type": "text_delta", "text": "答"}
                )
                self.chunks_yielded += 1

            if self.tool_use:
                # 工具调用已经发出去了，但结果还没等到就被按停 —— 最典型的半截状态。
                yield ContentBlockStartEvent(
                    index=1, content_block={"type": "tool_use", "id": "tu_1", "name": "x"}
                )
                await asyncio.sleep(0.01)
                yield ContentBlockDeltaEvent(
                    index=1,
                    delta={"type": "input_json_delta", "partial_json": "{}"},
                )
                self.chunks_yielded += 1

            # 空转：这���没有任何 chunk 到达，中止只能靠循环层的竞速唤醒。
            if self.hold is not None:
                await asyncio.sleep(self.hold)

            yield ContentBlockStopEvent(index=0)
            yield MessageDeltaEvent(
                delta={"stop_reason": "end_turn"},
                usage={"input_tokens": 10, "output_tokens": 10},
            )
            yield AssistantMessage(
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "答" * self.chunks}],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "stop_reason": "end_turn",
                }
            )
            self.completed = True
        finally:
            # 被取消时 CancelledError / GeneratorExit 都会走到这里。
            self.closed = True

    def count_tokens(
        self, messages: list[Any], system_prompt: str, tools: list[Any]
    ) -> int:
        return 100

    def normalize_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        return [m.message if hasattr(m, "message") else {} for m in messages]


async def _drain(engine: Any, text: str) -> list[Any]:
    return [msg async for msg in engine.submit_message(text)]


async def _wait_streaming(
    *providers: StallingProvider, at_least: int = 2, timeout: float = 5.0
) -> None:
    """等到每个 provider 都真的吐了 ``at_least`` 个 chunk。

    不能用固定 sleep 同步：查询从 submit 到发出第一次模型请求要跑预处理管线、
    建工具 schema 等一串准备动作，耗时不稳定 —— sleep 短了会在 provider 还没被
    调用时就 abort，测到的其实是"循环开头就退出"，根本不是流式中止。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(p.chunks_yielded >= at_least for p in providers):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(
        "provider 没在预期时间内进入流式阶段："
        + ", ".join(f"{p.name} yielded={p.chunks_yielded}" for p in providers)
    )


def _result(messages: list[Any]) -> SDKResultMessage:
    return next(m for m in messages if isinstance(m, SDKResultMessage))


class TestAbortDuringStreaming:
    """流式输出期间按停止。"""

    @pytest.mark.asyncio
    async def test_abort_interrupts_stalled_stream_promptly(self):
        """核心回归：provider 长时间不出字时，中止也能立刻把它拽出来。

        ``hold=30`` 秒代表"永远推不出下一个 chunk"。中止后必须几百毫秒内收摊。
        修复前循环阻塞在 ``__anext__`` 上，这里会真的等满 30 秒。
        """
        provider = StallingProvider(chunks=3, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "讲个长故事"))
            await _wait_streaming(provider)
            started = time.monotonic()
            engine.abort("用户点了停止")
            messages = await task
            elapsed = time.monotonic() - started

        assert _result(messages).subtype == "aborted_streaming"
        # 用户自己按的停止不是故障 —— 算进失败率会把「提前收工」当成系统出错。
        assert _result(messages).is_error is False
        assert elapsed < 0.5, f"等了 {elapsed:.2f}s 才停，没能打断空转"
        assert provider.completed is False, "流被读完了，说明根本没在中途停"
        assert provider.closed is True, "生成器没收尾，连接会一直挂着"

    @pytest.mark.asyncio
    async def test_abort_does_not_trigger_api_retry(self):
        """中止不能被 RecoveryChain 判成 API 错误再重试一次。

        这是个真实踩过的坑：中止一旦走异常通道，循环会判成失败并自动重发请求，
        用户点了停止反而又被续上。
        """
        provider = StallingProvider(chunks=3, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "跑久一点"))
            await _wait_streaming(provider)
            engine.abort()
            messages = await task

        assert provider.calls == 1, f"中止后又发了 {provider.calls - 1} 次请求"
        assert _result(messages).subtype == "aborted_streaming"

    @pytest.mark.asyncio
    async def test_abort_before_first_chunk_still_stops(self):
        """首 token 之前按停止 —— 同样是「长时间不出字」，必须能停。"""
        provider = StallingProvider(chunks=0, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "还没开始说"))
            await _wait_streaming(provider, at_least=0)
            # 等 provider 真的进了 stream（calls 变了才算）
            deadline = time.monotonic() + 5.0
            while provider.calls == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            assert provider.calls == 1

            started = time.monotonic()
            engine.abort()
            messages = await task
            elapsed = time.monotonic() - started

        assert _result(messages).subtype == "aborted_streaming"
        assert elapsed < 0.5, f"等了 {elapsed:.2f}s 才停"


class TestAbortSessionIsolation:
    """session 独享模式：停 A 不影响 B。"""

    @pytest.mark.asyncio
    async def test_aborting_one_session_leaves_the_other_running(self):
        """B 在 A 被中止的那一刻正处于「不出字」的空转期，且必须自己走完。

        这是最强的隔离断言：B 卡在自己的 ``await sleep`` 里，如果 A 的中止信号
        泄漏成了全局信号，B 会跟着一起死。
        """
        provider_a = StallingProvider(chunks=3, hold=30.0)  # 会被中止
        provider_b = StallingProvider(chunks=3, hold=1.0)   # 自己收尾
        engine_a = create_test_engine(provider=provider_a)
        engine_b = create_test_engine(provider=provider_b)

        async with engine_a, engine_b:
            task_a = asyncio.create_task(_drain(engine_a, "A 的问题"))
            task_b = asyncio.create_task(_drain(engine_b, "B 的问题"))

            # 等 A 和 B 都进到流式阶段，确保中止 A 时 B 正在输出中
            await _wait_streaming(provider_a, provider_b)

            engine_a.abort("只停 A")  # 只中止 A

            messages_a, messages_b = await asyncio.gather(task_a, task_b)

        assert _result(messages_a).subtype == "aborted_streaming"
        assert _result(messages_b).subtype == "success"
        assert provider_a.completed is False
        assert provider_b.completed is True, "B 被 A 的中止带偏了"

    @pytest.mark.asyncio
    async def test_abort_signal_is_per_engine_not_shared(self):
        """两个引擎各持各的 AbortSignal，停一个不会把另一个置位。"""
        engine_a = create_test_engine(provider=StallingProvider(chunks=2))
        engine_b = create_test_engine(provider=StallingProvider(chunks=2))

        assert engine_a._abort_controller.signal is not engine_b._abort_controller.signal

        engine_a.abort()
        assert engine_a._abort_controller.signal.aborted is True
        assert engine_b._abort_controller.signal.aborted is False

    @pytest.mark.asyncio
    async def test_second_session_still_runs_after_first_aborted(self):
        """A 中止之后**新发起**的 B 也不受残留信号影响。

        中止只活在 A 的控制器上；B 的引擎与控制器都是独立的。
        """
        provider_a = StallingProvider(chunks=3, hold=30.0)
        engine_a = create_test_engine(provider=provider_a)

        async with engine_a:
            task_a = asyncio.create_task(_drain(engine_a, "A 的问题"))
            await _wait_streaming(provider_a)
            engine_a.abort()
            await task_a

            provider_b = StallingProvider(chunks=2)
            engine_b = create_test_engine(provider=provider_b)
            async with engine_b:
                messages_b = await _drain(engine_b, "B 的问题")

        assert _result(messages_b).subtype == "success"
        assert provider_b.completed is True


class TestAbortedContentIsCommitted:
    """中止时已经流出来的内容要落库 —— 用户看见过，模型下一轮也得看得见。

    丢掉它的后果有两条：模型不知道自己刚说过什么，会在断句处接着编；
    半截 tool_use 没了配对的 tool_result，下一轮请求会被 API 直接拒掉。
    """

    def test_frontend_marker_matches_backend(self):
        """前后端的中止标记必须逐字一致。

        对不上的后果很具体：前端认不出后端写的标记块，就会把
        ``[用户已取消…]`` 原样显示在用户已经读过的正文下面 —— 正是这个功能
        本来要消灭的那种重复。所以这不是"顺手对齐一下"，是一条要挡住的回归。
        """
        import re
        from pathlib import Path

        from nono_agent.core.loop import ABORT_MARKER

        ts = Path(__file__).resolve().parents[1] / "apps/web/src/lib/abort-marker.ts"
        if not ts.exists():  # pragma: no cover —— 只装 Python 包时没有前端源码
            pytest.skip("前端源码不在（只装了 Python 包）")
        source = ts.read_text(encoding="utf-8")
        found = re.search(r'ABORT_MARKER = "([^"]+)"', source)
        assert found is not None, "abort-marker.ts 里找不到 ABORT_MARKER 定义"
        assert found.group(1) == ABORT_MARKER, (
            f"中止标记不一致：后端 {ABORT_MARKER!r}，前端 {found.group(1)!r}"
        )

    @staticmethod
    def _assistant_texts(engine: Any) -> list[str]:
        from nono_agent.core.types import AssistantMessage

        out: list[str] = []
        for msg in engine._store.all_messages():
            if not isinstance(msg, AssistantMessage):
                continue
            out.append(
                "\n".join(
                    block.get("text", "")
                    for block in msg.message.get("content", [])
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
        return out

    @pytest.mark.asyncio
    async def test_partial_output_is_kept_and_marked(self):
        """半截正文进历史，末尾带中止标记，stop_reason 也标成 aborted。"""
        from nono_agent.core.loop import ABORT_MARKER
        from nono_agent.core.types import AssistantMessage

        provider = StallingProvider(chunks=3, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "写点东西"))
            await _wait_streaming(provider)
            engine.abort()
            await task

            assistant = [
                msg
                for msg in engine._store.all_messages()
                if isinstance(msg, AssistantMessage)
            ]

        assert assistant, "中止后一条 assistant 消息都没留下 —— 内容被丢了"
        content = assistant[-1].message
        text = "\n".join(
            block.get("text", "")
            for block in content.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        # 具体几个 chunk 取决于中止落在哪次 sleep 上，不写死；要有正文、要有标记。
        assert "答" in text, "用户已经看到的半截正文没进历史"
        assert text.rstrip().endswith(ABORT_MARKER), "末尾没有中止标记"
        assert content.get("stop_reason") == "aborted"

    @pytest.mark.asyncio
    async def test_abort_before_any_output_adds_no_marker(self):
        """一个字都没吐就停 —— 不该凭空造一条只有标记的 assistant 消息。"""
        provider = StallingProvider(chunks=0, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "还没开口"))
            deadline = time.monotonic() + 5.0
            while provider.calls == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            engine.abort()
            await task

            texts = self._assistant_texts(engine)

        assert all("用户已取消" not in text for text in texts), (
            "没有输出却补了中止标记"
        )

    @pytest.mark.asyncio
    async def test_interrupted_tool_use_gets_placeholder_result(self):
        """半截 tool_use 必须补占位 tool_result —— 否则下一轮请求会被 API 拒掉。

        模型一边调用工具一边被按停是很常见的场景：tool_use 已经进了流，结果没等到。
        """
        from nono_agent.core.types import AssistantMessage, UserMessage

        provider = StallingProvider(chunks=1, hold=30.0, tool_use=True)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "去开网页"))
            await _wait_streaming(provider, at_least=2)
            engine.abort()
            await task
            messages = engine._store.all_messages()

        tool_uses: set[str] = set()
        for msg in messages:
            if isinstance(msg, AssistantMessage):
                for block in msg.message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_uses.add(block.get("id", ""))

        assert tool_uses, "测试前提没成立：assistant 消息里应当有 tool_use 块"

        results: set[str] = set()
        for msg in messages:
            if isinstance(msg, UserMessage):
                for block in msg.message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results.add(block.get("tool_use_id", ""))

        missing = tool_uses - results
        assert not missing, f"这些 tool_use 没有配对的 tool_result：{missing}"

    @pytest.mark.asyncio
    async def test_no_placeholder_when_no_tool_use(self):
        """没有 tool_use 时不该凭空造占位结果消息。"""
        from nono_agent.core.types import UserMessage

        provider = StallingProvider(chunks=3, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "只是说话"))
            await _wait_streaming(provider)
            engine.abort()
            await task
            messages = engine._store.all_messages()

        tool_results = [
            block
            for msg in messages
            if isinstance(msg, UserMessage)
            for block in msg.message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert not tool_results, "没有 tool_use 却补了占位 tool_result"

    @pytest.mark.asyncio
    async def test_next_turn_after_abort_succeeds(self):
        """中止之后还能接着对话 —— 历史里没有悬空的 tool_use，门禁也不会拦。"""
        provider_a = StallingProvider(chunks=3, hold=30.0)
        engine = create_test_engine(provider=provider_a)

        async with engine:
            task = asyncio.create_task(_drain(engine, "第一轮"))
            await _wait_streaming(provider_a)
            engine.abort()
            await task

            provider_b = StallingProvider(chunks=2, hold=None)
            engine_b_provider = provider_b
            # 同一个引擎换 provider 不方便，直接用脚本化 provider 跑第二轮
            followup = await _drain_with(engine, provider_b, "继续")

        assert _result(followup).subtype == "success", "中止后第二轮起不来"
        assert engine_b_provider.completed is True


async def _drain_with(engine: Any, provider: StallingProvider, text: str) -> list[Any]:
    """换掉引擎的 provider 再跑一轮 —— 用来验证中止后的续聊。"""
    engine._provider_registry.register_instance("claude-test", provider)
    return [msg async for msg in engine.submit_message(text)]


class TestAbortIsNotAFailure:
    """「用户主动停止」不属于失败情况 —— 终态分类的契约。

    这条曾经是错的：中止和真正的失败一样上报 ``is_error=true``，于是失败率统计里
    混进「用户提前收工」，CLI 和界面还会为此打红字。用户是主动点的停止，
    那里根本没有故障。
    """

    def test_aborted_terminals_are_declared_as_non_failure(self):
        from nono_agent.core.state import ABORTED_TERMINALS, TerminalReason

        assert TerminalReason.ABORTED_STREAMING.name in ABORTED_TERMINALS
        assert TerminalReason.ABORTED_TOOLS.name in ABORTED_TERMINALS
        # 真正的故障不能被顺手划进去。
        for real_error in (
            TerminalReason.MODEL_ERROR,
            TerminalReason.PROMPT_TOO_LONG,
            TerminalReason.IMAGE_ERROR,
        ):
            assert real_error.name not in ABORTED_TERMINALS

    @pytest.mark.asyncio
    async def test_abort_reports_non_error_subtype(self):
        """中止：subtype=aborted_streaming 且 is_error=False。"""
        provider = StallingProvider(chunks=2, hold=30.0)
        engine = create_test_engine(provider=provider)

        async with engine:
            task = asyncio.create_task(_drain(engine, "说一半"))
            await _wait_streaming(provider)
            engine.abort()
            messages = await task

        result = _result(messages)
        assert result.subtype == "aborted_streaming"
        assert result.is_error is False
        # 停不是错，就不该凭空造一条错误描述。
        assert not result.errors, f"中止不该带错误信息：{result.errors}"

    @pytest.mark.asyncio
    async def test_normal_completion_still_reports_success(self):
        """对照组：正常结束不能被这次改动带偏。"""
        provider = StallingProvider(chunks=2, hold=None)
        engine = create_test_engine(provider=provider)

        async with engine:
            messages = await _drain(engine, "说完")

        result = _result(messages)
        assert result.subtype == "success"
        assert result.is_error is False


class TestIterateWithAbort:
    """``iterate_with_abort`` 本身的语义。"""

    @pytest.mark.asyncio
    async def test_yields_everything_when_not_aborted(self):
        controller = AbortController()

        async def source():
            for i in range(5):
                yield i

        assert [x async for x in iterate_with_abort(source(), controller.signal)] == [
            0,
            1,
            2,
            3,
            4,
        ]

    @pytest.mark.asyncio
    async def test_yields_nothing_when_already_aborted(self):
        controller = AbortController()
        controller.abort()

        async def source():
            yield 1  # pragma: no cover —— 不该被拉到

        assert [x async for x in iterate_with_abort(source(), controller.signal)] == []

    @pytest.mark.asyncio
    async def test_stops_at_abort_midway_and_closes_source(self):
        controller = AbortController()
        closed = False

        async def source():
            nonlocal closed
            try:
                for i in range(100):
                    await asyncio.sleep(0.02)
                    yield i
            finally:
                closed = True

        seen: list[int] = []

        async def consume():
            async for item in iterate_with_abort(source(), controller.signal):
                seen.append(item)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        controller.abort()
        await task

        assert 0 < len(seen) < 100, f"应该中途停下，实际收了 {len(seen)} 个"
        assert closed is True, "源没被收尾，连接会挂着"

    @pytest.mark.asyncio
    async def test_interrupts_source_that_never_yields_again(self):
        """源卡住不再产出时也能停 —— 这一条是「停止按钮毫无反应」的真正现场。"""
        controller = AbortController()

        async def source():
            yield 1
            await asyncio.sleep(30)  # 永远到不了的下一行
            yield 2  # pragma: no cover

        seen: list[int] = []

        async def consume():
            async for item in iterate_with_abort(source(), controller.signal):
                seen.append(item)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        started = time.monotonic()
        controller.abort()
        await task
        elapsed = time.monotonic() - started

        assert seen == [1]
        assert elapsed < 0.5, f"等了 {elapsed:.2f}s，没能打断卡住的源"

    @pytest.mark.asyncio
    async def test_closes_source_even_on_normal_completion(self):
        """正常读完也要 aclose —— 否则 SDK 连接池里挂着半开的流。"""
        controller = AbortController()
        closed = False

        async def source():
            nonlocal closed
            try:
                yield 1
                yield 2
            finally:
                closed = True

        assert [x async for x in iterate_with_abort(source(), controller.signal)] == [1, 2]
        assert closed is True

    @pytest.mark.asyncio
    async def test_tolerates_none_signal(self):
        """signal=None 表示不设中止控制，照常消费到底。"""

        async def source():
            yield 1

        assert [x async for x in iterate_with_abort(source(), None)] == [1]
