"""实测：图片生成的工具调用在飞的时候，服务端还能不能响应别的请求。

复现「发起图片生成后，文件树加载不出来子目录和文件内容」。

做法：

1. 起一个**假的 MiniMax**：``/v1/images/generations`` 先睡 N 秒，再回 H3-Image 的形状。
2. 用**真实的插件清单**装配出 ``minimax__generate_image``，``base_url`` 指到假服务器。
3. 工具调用在飞的时候，并发打 ``/app/fs/entries``（文件树接口），量它的延迟。
4. 同时量事件循环的卡顿（lag）—— 事件循环被占死的话，lag 会飙上去。

清单里的 ``background.wait_s`` 让这次调用在几秒后**转后台**：对话那条连接立刻
释放，图片继续在后台生成并落盘。

跑法：``python runtime-test/probe_image_tool_blocks.py``。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "server"))

from nono_agent.shell.jobs import JobRegistry  # noqa: E402
from nono_agent.toolplugins.http_json import HttpJsonTool  # noqa: E402
from nono_agent.toolplugins.spec import parse_tool_specs  # noqa: E402
from nono_agent.tools.context import ToolUseContext  # noqa: E402
from nono_app_server.features.files.api import router as files_router  # noqa: E402

PLUGIN_DIR = (
    REPO_ROOT
    / "apps"
    / "server"
    / "nono_app_server"
    / "features"
    / "toolplugins"
    / "plugins"
    / "minimax_image"
)

GENERATE_DELAY_S = 20.0   # 假厂商要等多久
WAIT_S = 2.0              # 前台等多久就转后台
B64_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeMiniMax:
    """一个只会「先睡后回」的假 MiniMax。"""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.hits = 0
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            if length:
                await reader.readexactly(length)
            self.hits += 1
            await asyncio.sleep(self.delay_s)
            body = json.dumps(
                {
                    "created": 1792308678,
                    "data": [{"b64_json": B64_PNG, "size": "2048x2048"}],
                    "output_format": "jpeg",
                    "usage": {"generated_images": 1},
                    "request_id": "probe",
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


def build_tool(base_url: str, wait_s: float) -> HttpJsonTool:
    """用真实清单装配工具，只把 base_url 与前台等待秒数换掉。"""
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    specs = parse_tool_specs(manifest["name"], manifest["tools"])
    spec = next(s for s in specs if s.tool_name == "minimax__generate_image")
    options = {**spec.defaults, "base_url": base_url, "image_wait_s": wait_s}
    return HttpJsonTool(spec, options, "probe-key")  # type: ignore[arg-type]


async def probe_lag(stop: asyncio.Event, sink: list[float]) -> None:
    """事件循环卡顿：每次 sleep(0.01) 实际多花了多久。"""
    while not stop.is_set():
        started = time.perf_counter()
        await asyncio.sleep(0.01)
        sink.append(time.perf_counter() - started - 0.01)
        await asyncio.sleep(0.01)


async def main() -> int:
    import nono_agent.toolplugins.http_json as http_json

    # 假服务器在回环上，SSRF 闸门会拦 —— 这里要测的是「等待期间」的表现，
    # 闸门本身另有用例。
    http_json.is_url_blocked = lambda *a, **k: False  # type: ignore[assignment]

    work = REPO_ROOT / "runtime-test" / ".probe-workspace"
    work.mkdir(parents=True, exist_ok=True)
    (work / "hello.txt").write_text("hi", encoding="utf-8")
    for stale in (work / ".nono-agent" / "images").glob("*.png"):
        stale.unlink()

    app = FastAPI()
    app.include_router(files_router, prefix="/app")

    fake = FakeMiniMax(GENERATE_DELAY_S)
    await fake.start()
    tool = build_tool(f"http://127.0.0.1:{fake.port}", WAIT_S)

    stop = asyncio.Event()
    lag: list[float] = []
    registry = JobRegistry("probe")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://probe"
    ) as client:
        params = {"root": str(work)}
        baseline = time.perf_counter()
        resp = await client.get("/app/fs/entries", params=params)
        baseline_ms = (time.perf_counter() - baseline) * 1000
        print(f"基线（无工具调用）: /app/fs/entries {resp.status_code} {baseline_ms:.0f}ms")

        probe = asyncio.create_task(probe_lag(stop, lag))
        context = ToolUseContext(cwd=str(work), shell_jobs=registry)
        started = time.perf_counter()
        result = await tool.execute({"prompt": "a cat"}, context)
        elapsed = (time.perf_counter() - started) * 1000
        print(
            f"工具调用返回: {elapsed:.0f}ms  is_error={result.is_error}"
            f"（厂商那边要等 {GENERATE_DELAY_S:.0f}s）"
        )
        print(f"返回内容: {result.content.splitlines()[0]}")

        started = time.perf_counter()
        resp = await client.get("/app/fs/entries", params=params)
        during_ms = (time.perf_counter() - started) * 1000
        print(f"转后台后立刻打 /app/fs/entries: {resp.status_code} {during_ms:.0f}ms")

        started = time.perf_counter()
        resp = await client.get("/app/fs/file", params={**params, "path": "hello.txt"})
        file_ms = (time.perf_counter() - started) * 1000
        print(f"转后台后立刻打 /app/fs/file: {resp.status_code} {file_ms:.0f}ms")

        # 等后台收尾：图片应当照样落盘
        deadline = time.monotonic() + GENERATE_DELAY_S + 5
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            if any((work / ".nono-agent" / "images").glob("*.png")):
                break
        saved = list((work / ".nono-agent" / "images").glob("*.png"))
        print(f"后台收尾后落盘: {[p.name for p in saved]}")

        stop.set()
        await probe
        await fake.stop()

    worst = max(lag) if lag else 0.0
    print(f"事件循环卡顿: 最大 {worst * 1000:.0f}ms，采样 {len(lag)} 次")

    ok = (
        elapsed < WAIT_S * 1000 + 1500
        and during_ms < 2000
        and file_ms < 2000
        and worst < 0.5
        and len(saved) == 1
    )
    print(
        "\n"
        + (
            "对话连接已提前释放，文件树接口全程可用，图片仍在后台生成并落盘。"
            if ok
            else "**不符合预期。**"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
