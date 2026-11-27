"""探测四：中止发生在首个 token 之前时,落库与下一轮是否正常。

背景：断连兜底要走 abort 语义,而 abort 可能在模型吐出第一个字之前就到达。
      那一刻 ``assistant_content`` 里只有 content_block_start 的空 text 块 ——
      落库后模型上下文里会不会留一条「空回复」，把下一轮带歪？

跑法：``python runtime-test/probe_abort_before_first_token.py``
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
    ContentBlockStartEvent,
    MessageStartEvent,
)
from nono_agent.session.transcript import LocalFileTranscriptWriter  # noqa: E402
from tests.helpers import create_test_engine  # noqa: E402


class SilentProvider:
    """开了 content_block_start 就一直不吐字的 provider —— 模拟「首 token 之前被中止」。"""

    def __init__(self) -> None:
        self.closed = False
        self.completed = False

    @property
    def name(self) -> str:
        return "silent"

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
        try:
            yield MessageStartEvent(message={"usage": {"input_tokens": 10, "output_tokens": 0}})
            yield ContentBlockStartEvent(index=0, content_block={"type": "text", "text": ""})
            await asyncio.sleep(30.0)  # 永远不吐字
            yield AssistantMessage(  # pragma: no cover - 走不到
                message={"role": "assistant", "content": [{"type": "text", "text": "x"}]}
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


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="nono-empty-"))
    writer = LocalFileTranscriptWriter(tmp / "sess.jsonl", compress=False)
    provider = SilentProvider()
    engine = create_test_engine(
        provider=provider,
        transcript_writer=writer,
        config_overrides={"session_id": "sess"},
    )

    submit = engine.submit_message("第一问")
    sdk_queue: asyncio.Queue[Any] = asyncio.Queue()

    async def drain(agen: Any, out: Any) -> None:
        async for item in agen:
            await out.put(item)

    sdk_task = asyncio.create_task(drain(submit, sdk_queue))
    await asyncio.wait_for(sdk_queue.get(), timeout=5)  # 等引擎真的跑起来
    await asyncio.sleep(0.1)

    print("[客户端] 首个 token 之前 —— 调用 engine.abort()")
    engine.abort("client disconnected")
    try:
        await asyncio.wait_for(sdk_task, timeout=10)
    except asyncio.TimeoutError:
        print("  !! abort 后 10s 仍未收尾")
    except Exception as exc:
        print(f"  流异常：{type(exc).__name__}: {exc}")

    events = await writer.read_events()
    types = [e.type for e in events]
    print(f"\n落盘事件类型：{types}")

    from nono_agent.session import derive_messages

    derived = derive_messages(events)
    print(f"\nderive_messages 推出 {len(derived)} 条：")
    for m in derived:
        print(f"  - {m.get('role')}: {m.get('content')!r}")

    assistant_msgs = [m for m in derived if m.get("role") == "assistant"]
    if assistant_msgs:
        content = assistant_msgs[-1].get("content")
        blocks = content if isinstance(content, list) else []
        empty = [b for b in blocks if b.get("type") == "text" and not b.get("text")]
        print(f"\n  assistant 消息里的空 text 块数：{len(empty)}")

    print("\n===== 第二轮：模型上下文里带上一条空回复,还能不能正常问 =====")
    try:
        msgs = []
        async for msg in engine.submit_message("第二问"):
            msgs.append(msg.type)
        print(f"  第二轮成功，终态 {msgs[-1] if msgs else '无'}")
    except Exception as exc:
        print(f"  第二轮失败：{type(exc).__name__}: {exc}")

    sent = provider  # MockProvider 替身；这里只关心第二轮是否抛错


if __name__ == "__main__":
    asyncio.run(main())
