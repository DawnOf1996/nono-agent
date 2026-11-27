"""验证「同源连接数上限」这条机制：长连接占满后，普通请求会不会排队。

做法：起一个小站点，同源提供三条路由：

- ``/stream`` —— 一直不结束的 SSE（模拟 control / browser / stats / fs-watch 这类常驻流）
- ``/api/data`` —— 普通 JSON（模拟文件树的 ``/app/fs/entries``）
- ``/`` —— 页面自己开 N 条 ``/stream``，再发一条 ``/api/data``，把耗时写进 DOM

浏览器对同一 origin 的 HTTP/1.1 并发连接有上限（Chromium 是 6）。长连接把名额占满
之后，普通请求就只能排队 —— 这正是「图片生成期间文件树加载不出来」的形状。

跑法：``python runtime-test/probe_connection_budget.py``（默认 8793 端口）。
"""

from __future__ import annotations

import asyncio
import sys

HOST = "127.0.0.1"
PORT = 8793
STREAMS = 6

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>probe</title></head>
<body>
<pre id="out">running…</pre>
<script>
async function main() {
  const out = document.getElementById("out");
  const log = (s) => { out.textContent += "\\n" + s; };
  const controllers = [];
  for (let i = 0; i < %d; i++) {
    const c = new AbortController();
    controllers.push(c);
    // 一条永不结束的 SSE：占住一个连接名额
    fetch("/stream", { signal: c.signal, headers: { Accept: "text/event-stream" } })
      .then(async (r) => { const reader = r.body.getReader(); for (;;) { await reader.read(); } })
      .catch(() => {});
  }
  await new Promise((r) => setTimeout(r, 800));   // 等这些连接真的建起来
  const t0 = performance.now();
  let outcome = "";
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);   // 4 秒还��回来 = 被排队卡住
    const r = await fetch("/api/data", { signal: ctrl.signal });
    clearTimeout(timer);
    const body = await r.text();
    outcome = "api/data 返回 " + r.status + " body=" + body.trim();
  } catch (e) {
    outcome = "api/data 4 秒没回来（被同源连接数上限卡住）: " + e.name;
  }
  const took = Math.round(performance.now() - t0);
  log(outcome);
  log("耗时 " + took + "ms");
  document.title = "DONE " + took + "ms";
  window.__probe = { outcome, took };
  // 再放掉一条连接，看排队中的请求是否立刻放行
  setTimeout(() => {
    controllers.pop().abort();
    const t1 = performance.now();
    fetch("/api/data").then(async (r) => {
      const body = await r.text();
      window.__probe.afterRelease = { took: Math.round(performance.now() - t1), body: body.trim() };
      document.title = "DONE " + took + "ms / released " + window.__probe.afterRelease.took + "ms";
    });
  }, 200);
}
main();
</script>
</body></html>
"""


class Probe:
    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, HOST, PORT)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            path = line.split(" ")[1] if " " in line else "/"
            length = 0
            for raw in head.split(b"\r\n"):
                if raw.lower().startswith(b"content-length:"):
                    length = int(raw.split(b":", 1)[1].strip())
            if length:
                await reader.readexactly(length)

            if path.startswith("/stream"):
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Cache-Control: no-cache\r\n\r\n"
                )
                await writer.drain()
                while True:  # 永不结束 —— 这就是要占住名额的那条连接
                    writer.write(b": keepalive\n\n")
                    await writer.drain()
                    await asyncio.sleep(1.0)
            elif path.startswith("/api/data"):
                body = b'{"ok":true}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
            else:
                body = (PAGE % STREAMS).encode("utf-8")
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


async def main() -> int:
    probe = Probe()
    await probe.start()
    print(f"探针站点已起：http://{HOST}:{PORT}/  （开 {STREAMS} 条常驻流）")
    print("用浏览器打开它，看页面标题里的 DONE xxx ms。")
    while True:
        await asyncio.sleep(3600)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
