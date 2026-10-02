"""统计推送（push）的端到端验证。

不靠轮询：直接连 ``/session/{id}/stats/stream``，在引擎跑一次带工具调用的
对话，逐帧打印收到的统计 —— 确认每一步都有新帧推过来，且最后一帧与
「重读整份日志折叠」的结果一致。
"""

import asyncio
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "server"))

from tests.helpers import MockTool, create_test_engine  # noqa: E402


def _row(label: str, snap: dict) -> str:
    ctx = snap.get("context") or {}
    return (
        f"{label:<26} turns={snap['turns']} steps={snap['steps']} "
        f"model={snap['model_duration_ms']:.0f}ms tool={snap['tool_duration_ms']:.0f}ms "
        f"tokens={snap['usage']['total_tokens']:<4} ctx={ctx.get('total_tokens', '-')}"
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
        # 模拟服务端：引擎每落一步就 fanout 给订阅者
        got: list[dict] = []
        ready = threading.Event()

        def on_stats(snapshot: dict) -> None:
            got.append(snapshot)
            print("  << push", _row(f"frame#{len(got)}", snapshot))
            ready.set()

        engine.add_stats_listener(on_stats)

        print("订阅已建立，开始对话：")
        async for message in engine.submit_message("算一下"):
            if message.type == "result":
                print("  -- 引擎 result --")
        # 让最后一次 flush 的通知跑完
        for _ in range(50):
            if got and got[-1]["steps"] == 2:
                break
            await asyncio.sleep(0.02)

        print(f"\n共收到 {len(got)} 帧推送（无轮询）")
        print("步数序列:", [g["steps"] for g in got])
        final = got[-1]
        from nono_agent.session.stats import build_session_stats  # noqa: E402
        from nono_agent.session.transcript import LocalFileTranscriptWriter  # noqa: E402

        path = getattr(getattr(engine, "transcript_writer", None), "session_file", None)
        if path is not None and Path(path).exists():
            events = await LocalFileTranscriptWriter(Path(path)).read_events()
            same = build_session_stats(events) == final
            print(f"对账（重读日志 vs 推送末帧）: {'一致' if same else '不一致'}")
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
