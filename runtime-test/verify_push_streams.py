"""起一个真的 uvicorn，验证「不再轮询」：客户端不主动请求，服务端一变就推。

浏览器端的 ``setInterval`` 都已经拿掉了，这段脚本盯住剩下的问题 ——
改完之后，数据到底还来不来？做法是**只读那条流，绝不再发一次查询请求**：
连上、拿到当前值，然后在磁盘上动一下，等服务端自己把新状态推过来。

注意两条流的承载方式不一样了：

- **控制流**走 WebSocket（``/app/control/ws``）—— 常驻长连接改走 WS，落到浏览器
  独立的 socket pool，不再抢同一 origin 的 HTTP/1.1 名额（Chromium 每 host
  只有 6 条）。
- **git 流**仍是 SSE（``/app/git/stream``）—— 它按需开，不是常驻。

帧的写法两者一样（``event: x`` / ``data: {...}``），换的只是承载它的连接。

跑法：``python runtime-test/verify_push_streams.py``。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

HOST = "127.0.0.1"
PORT = 8791
BASE = f"http://{HOST}:{PORT}"
REPO_ROOT = Path(__file__).resolve().parent.parent


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args], cwd=cwd, check=True, capture_output=True
    )


def build_repo() -> Path:
    work = Path(tempfile.mkdtemp(prefix="nono-push-")) / "work"
    work.mkdir(parents=True)
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "core.autocrlf", "false")
    (work / "app.py").write_text("a = 1\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    return work


async def sse_frames(response: httpx.Response):
    """一条 SSE 连接只能读一遍，所以整个连接共用这一个生成器。"""
    event = ""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith(":"):
            continue  # 保活注释，不是数据
        if line == "":
            if event:
                yield (event, "\n".join(data))
            event, data = "", []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


async def next_frame(frames, timeout: float = 20.0) -> tuple[str, str]:
    return await asyncio.wait_for(anext(frames), timeout=timeout)


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK ' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    return ok


def parse_frame(text: str) -> tuple[str, str]:
    """拆一帧（WebSocket 文本帧 / SSE 帧的写法一样）。"""
    event = ""
    data: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    return event, "\n".join(data)


async def verify_control() -> bool:
    """控制流走 WebSocket —— 常驻连接不占 HTTP/1.1 的名额。"""
    async with websockets.connect(f"ws://{HOST}:{PORT}/app/control/ws") as socket:
        health_event, health_raw = parse_frame(
            await asyncio.wait_for(socket.recv(), timeout=20)
        )
        perms_event, perms_raw = parse_frame(
            await asyncio.wait_for(socket.recv(), timeout=20)
        )
        if not check(
            "控制流连上即得两帧",
            [health_event, perms_event] == ["health", "permissions"],
            f"{health_event}, {perms_event}",
        ):
            return False
        health = json.loads(health_raw)
        ok = check(
            "health 帧内容正确",
            health.get("status") == "ok" and isinstance(health.get("sessions"), int),
            str(health),
        )
        return check("permissions 帧内容正确", json.loads(perms_raw) == {"permissions": []}) and ok


async def verify_git_push(client: httpx.AsyncClient, work: Path) -> bool:
    """关键的一条：改了磁盘之后不碰任何查询接口，等服务端推。"""
    root = httpx.QueryParams({"root": str(work)})
    async with client.stream("GET", f"{BASE}/app/git/stream", params=root) as resp:
        if not check(
            "git 流是 SSE",
            resp.headers.get("content-type", "").startswith("text/event-stream"),
            resp.headers.get("content-type", ""),
        ):
            return False
        frames = sse_frames(resp)
        first = await next_frame(frames)
        second = await next_frame(frames)
        if not check(
            "git 流连上即得 status + worktrees",
            [first[0], second[0]] == ["status", "worktrees"],
            f"{first[0]}, {second[0]}",
        ):
            return False
        status = json.loads(first[1])
        if not check(
            "初始状态干净",
            status["is_repo"] and status["entries"] == [],
            str(status.get("entries")),
        ):
            return False

        # 只在磁盘上动手：没有 HTTP 请求，没有轮询。
        (work / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        started = time.monotonic()
        pushed = await next_frame(frames)
        took = time.monotonic() - started
        if not check("磁盘一改，服务端自己推", pushed[0] == "status", f"耗时 {took:.1f}s"):
            return False
        entries = json.loads(pushed[1])["entries"]
        return check(
            "推来的状态带着新改动",
            [item["path"] for item in entries] == ["app.py"]
            and entries[0]["unstaged_stat"]["added"] == 1,
            str(entries),
        )


async def main() -> int:
    work = build_repo()
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "nono_app_server.app:create_app", "--factory",
            "--host", HOST, "--port", str(PORT), "--log-level", "warning",
        ],
        cwd=REPO_ROOT,
    )
    try:
        for _ in range(160):
            if server.poll() is not None:
                print("FAIL  服务进程退出了")
                return 1
            try:
                if httpx.get(f"{BASE}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            print("FAIL  服务没起来")
            return 1

        ok = True
        async with httpx.AsyncClient(timeout=None) as client:
            ok = await verify_control() and ok
            ok = await verify_git_push(client, work) and ok
        print("\n" + ("全部通过：前端不再轮询，数据由服务端推过来。" if ok else "有检查没过。"))
        return 0 if ok else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            server.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
