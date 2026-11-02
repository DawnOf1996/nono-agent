"""客户端断开 vs 用户按停止 —— 两种中断方式对「这一轮」的影响完全不同。

背景：``apps/server/nono_app_server/engine/routers/chat.py`` 的 ``/chat`` 把
``engine.submit_message()`` 的产出倒进队列，再逐帧 yield 成 SSE。生成是
**拉模式（pull）** 的：``drain_to_queue`` 主动从生成器里拉，没人拉就不推进。

于是两种中断方式的结果完全不同：

- **用户按停止** → ``engine.abort()`` → 引擎主动收束本轮 → 正文落盘
  （``assistant/message`` + ``turn/end``）→ 重新打开历史能看到那半截回复。
- **客户端断开**（刷新页面 / 关标签 / 断网）→ ``sdk_task.cancel()`` →
  生成器被关闭 → 模型侧连接被拆、不再继续生成，**且这一轮的正文没落盘**。
  重开历史只能看到用户那一条，助手的半截回复整个消失。

跑法：``python runtime-test/probe_abort_vs_disconnect.py``
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nono_agent.core.types import (  # noqa: E402
    AssistantMessage,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    MessageDeltaEvent,
    MessageStartEvent,
)
from nono_agent.session.transcript import LocalFileTranscriptWriter  # noqa: E402
from tests.helpers import create_test_engine  # noqa: E402


class ChattyProvider:
    """一直吐字的 provider —— 用来观察中断后模型侧连接是否被拆。

    刻意不看 signal：真实 provider 阻塞在"等下一个 chunk"时没人能读信号，
    唤醒的责任在循环层（见 tests/test_abort.py 的说明）。
    """

    def __init__(self, chunks: int = 40, chunk_delay: float = 0.02) -> None:
        self.chunks = chunks
        self.chunk_delay = chunk_delay
        self.yielded = 0
        self.closed = False
        self.completed = False
        self.turns = 0

    @property
    def name(self) -> str:
        return "chatty"

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
        self.turns += 1
        try:
            yield MessageStartEvent(message={"usage": {"input_tokens": 10, "output_tokens": 0}})
            yield ContentBlockStartEvent(index=0, content_block={"type": "text", "text": ""})
            for _ in range(self.chunks):
                await asyncio.sleep(self.chunk_delay)
                yield ContentBlockDeltaEvent(
                    index=0, delta={"type": "text_delta", "text": "答"}
                )
                self.yielded += 1
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
            self.closed = True

    def count_tokens(
        self, messages: list[Any], system_prompt: str, tools: list[dict[str, Any]]
    ) -> int:
        return 100

    def normalize_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        return [m.message if hasattr(m, "message") else {} for m in messages]


async def run_case(name: str, interrupt) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="nono-cmp-"))
    writer = LocalFileTranscriptWriter(tmp / "sess.jsonl", compress=False)
    provider = ChattyProvider(chunks=40, chunk_delay=0.02)
    engine = create_test_engine(
        provider=provider,
        transcript_writer=writer,
        config_overrides={"session_id": "sess"},
    )

    # ---- 复刻 chat.py 的结构：生成器 → 队列 → SSE ----------------------
    submit = engine.submit_message("第一问")
    sdk_queue: asyncio.Queue[Any] = asyncio.Queue()

    async def drain(agen: Any, out: Any) -> None:
        async for item in agen:
            await out.put(item)

    sdk_task = asyncio.create_task(drain(submit, sdk_queue))

    # 客户端读了 12 帧就断开（此时模型已经吐了 9 个字）
    got = 0
    while got < 12:
        await asyncio.wait_for(sdk_queue.get(), timeout=5)
        got += 1

    await interrupt(engine, sdk_task)
    await asyncio.sleep(0.6)

    # ---- 断线瞬间的落盘状态 -------------------------------------------
    events = await writer.read_events()
    types = [e.type for e in events]
    print(f"\n########## {name} ##########")
    print(f"  provider 产出字数   : {provider.yielded}")
    print(f"  provider 已关闭     : {provider.closed}   <- 模型侧连接是否被拆")
    print(f"  provider 正常跑完   : {provider.completed}")
    print(f"  落盘事件类型        : {types}")
    print(f"  有 assistant/message: {'assistant/message' in types}")
    print(f"  有 turn/end         : {'turn/end' in types}")

    from nono_agent.session import derive_messages

    derived = derive_messages(events)
    print(f"  derive_messages 推出: {len(derived)} 条")
    for m in derived:
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        print(f"    - {m.get('role')}: {str(content)[:60]!r}")

    # ---- 断线后同一会话还能不能继续说话 -------------------------------
    provider.completed = False
    try:
        msgs = []
        async for msg in engine.submit_message("第二问"):
            msgs.append(msg.type)
        print(f"  断线后第二轮        : 成功（终态 {msgs[-1] if msgs else '无'}）")
        print(f"  provider 跑了几轮   : {provider.turns}")
    except Exception as exc:
        print(f"  断线后第二轮        : 失败 {type(exc).__name__}: {exc}")


async def main() -> None:
    async def do_abort(engine: Any, sdk_task: Any) -> None:
        engine.abort("用户按了停止")
        print("[客户端] 已调用 engine.abort()")

    async def do_disconnect(engine: Any, sdk_task: Any) -> None:
        sdk_task.cancel()
        print("[客户端] SSE 连接已断开（sdk_task.cancel()）")
        try:
            await sdk_task
        except BaseException:
            pass

    await run_case("A. 用户按停止（engine.abort）", do_abort)
    await run_case("B. 客户端断开（连接取消）", do_disconnect)


if __name__ == "__main__":
    asyncio.run(main())
