"""对着真服务器验证统计推送（不是 TestClient —— 无限 SSE 流会被它挂住）。

起一个真的 uvicorn，连上 ``/session/<id>/stats/stream``，然后在**另一条请求**
里让引擎跑一次带工具调用的对话，逐帧打印客户端收到的内容。

预期：每落一步事件就有一帧推过来，步数 0→1→2 单调递增走完，最后一帧
应当与 ``GET /session/<id>/stats`` 的一次性取值一致。
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "server"))


async def main() -> None:
    import httpx
    import uvicorn

    from nono_agent.session import transcript as transcript_module
    from nono_app_server.engine import create_server_app

    tmp = Path(tempfile.mkdtemp(prefix="stats-stream-"))
    transcript_module.DEFAULT_SESSION_DIR = tmp / "sessions"

    app = create_server_app(api_key=None)
    config = uvicorn.Config(app, host="127.0.0.1", port=8791, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    base = "http://127.0.0.1:8791"
    frames: list[dict] = []

    try:
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            created = await client.post("/session/new", json={"cwd": str(tmp)})
            sid = created.json()["session_id"]
            print(f"会话 {sid[:8]} 已创建，先取一次基线快照：")
            baseline = (await client.get(f"/session/{sid}/stats")).json()
            print(f"  steps={baseline['steps']} tokens={baseline['usage']['total_tokens']}")

            # 订阅（不轮询）
            async def listen() -> None:
                async with client.stream("GET", f"/session/{sid}/stats/stream") as resp:
                    assert resp.status_code == 200
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            frames.append(json.loads(line[5:].strip()))

            listener = asyncio.create_task(listen())
            await asyncio.sleep(0.4)  # 等订阅建立

            print("\n开始对话，同时客户端在收推送：")
            # 走 /chat 需要真实的 provider，这里改为在进程内直接驱动引擎：
            # 手动 stage + flush 事件，等价于引擎跑完一步，HTTP 侧照样收到回调。
            manager = app.state.engine_sessions
            engine = await manager.get_or_create(sid, model="dummy")
            # 直接喂事件：模拟引擎落盘 → flush → 统计变化 → fanout
            from nono_agent.session.events import (
                EVENT_ASSISTANT_CHUNK,
                EVENT_STEP_END,
                EVENT_STEP_START,
                EVENT_TURN_START,
                EVENT_USAGE,
            )

            seq = 0
            for type_, data, ts in [
                (EVENT_TURN_START, {}, 0.0),
                (EVENT_STEP_START, {}, 100.0),
                (EVENT_ASSISTANT_CHUNK, {"chunk": {}}, 200.0),
                (EVENT_ASSISTANT_CHUNK, {"chunk": {}}, 400.0),
                (EVENT_STEP_END, {}, 900.0),
                (
                    EVENT_USAGE,
                    {
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 20,
                            "cache_read_input_tokens": 100,
                            "cache_creation_input_tokens": 0,
                        },
                        "model": "m",
                    },
                    900.0,
                ),
                (EVENT_STEP_START, {}, 1000.0),
                (EVENT_ASSISTANT_CHUNK, {"chunk": {}}, 1100.0),
                (EVENT_STEP_END, {}, 1300.0),
                (
                    EVENT_USAGE,
                    {
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 8,
                            "cache_read_input_tokens": 130,
                            "cache_creation_input_tokens": 0,
                        },
                        "model": "m",
                    },
                    1300.0,
                ),
            ]:
                seq += 1
                engine._stage_event(type_, **data)
                await engine._flush_events()
                await asyncio.sleep(0.08)

            await asyncio.sleep(0.5)
            listener.cancel()

            print(f"\n客户端收到 {len(frames)} 帧：")
            for i, f in enumerate(frames, 1):
                print(f"  frame#{i:<3} steps={f['steps']} tokens={f['usage']['total_tokens']}")
            print("步数序列:", [f["steps"] for f in frames])

            final = (await client.get(f"/session/{sid}/stats")).json()
            same = frames[-1] == final if frames else False
            print(f"\n末帧与一次性 GET 一致: {'是' if same else '否'}")
            print(f"一次性 GET: steps={final['steps']} tokens={final['usage']['total_tokens']}")
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
