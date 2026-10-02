"""端到端：跑一次带工具调用的真实对话，验证统计随每一步前进。

这是手工验证脚本（不是 pytest）：跑一次 2 步的对话，**每收到一条 SDK 消息
就取一次** ``engine.session_stats()``，看步数/用时/token/上下文是不是在轮次
结束之前就已经在动；最后再和「重读整份日志折叠」的结果对账。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nono_agent.session.stats import build_session_stats  # noqa: E402
from nono_agent.session.transcript import LocalFileTranscriptWriter  # noqa: E402
from tests.helpers import MockTool, create_test_engine  # noqa: E402


def _row(label: str, snap: dict) -> str:
    context = snap.get("context") or {}
    return (
        f"{label:<10} turns={snap['turns']} steps={snap['steps']} "
        f"model={snap['model_duration_ms']:.0f}ms tool={snap['tool_duration_ms']:.0f}ms "
        f"ttft={(snap['ttft_ms'] or 0):.0f}ms tps={(snap['tps'] or 0):.1f} "
        f"tokens={snap['usage']['total_tokens']:<5} ctx={context.get('total_tokens', '-')}"
    )


async def main() -> None:
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "calc", "id": "t1", "input": {}}]},
            {"text": "42", "tool_calls": []},
        ],
        tools=[MockTool(name="calc", response="42")],
    )
    await engine.initialize()
    try:
        print("—— 每条 SDK 消息后取一次快照 ——")
        print(_row("初始", engine.session_stats()))
        n = 0
        async for message in engine.submit_message("算一下"):
            n += 1
            print(_row(f"msg#{n} {message.type}", engine.session_stats()))

        final = engine.session_stats()
        print("\n最终:", _row("done", final))

        writer = getattr(engine, "transcript_writer", None)
        path = getattr(writer, "session_file", None)
        if path is not None and Path(path).exists():
            events = await LocalFileTranscriptWriter(Path(path)).read_events()
            replayed = build_session_stats(events)
            print(f"\n对账（重读日志 vs 增量累加）: {'一致' if replayed == final else '不一致'}")
            for key in final:
                if replayed.get(key) != final[key]:
                    print(f"   {key}: log={replayed.get(key)} incr={final[key]}")
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
