/**
 * 流式渲染性能用的假 sidecar：只实现 web 前端启动与单轮长输出所需的接口。
 *
 *   node scripts/dev/mock-sidecar.mjs [--port 8000] [--tokens 2500] [--interval 4]
 *
 * /chat 会按 token 逐个吐 thinking_delta 与 text_delta，用来复现「单轮输出很多」时的卡顿。
 */
import http from "node:http";

const args = process.argv.slice(2);
function arg(name, fallback) {
  const at = args.indexOf(`--${name}`);
  return at >= 0 && args[at + 1] != null ? args[at + 1] : fallback;
}

const PORT = Number(arg("port", 8000));
const TOKENS = Number(arg("tokens", 2500));
const INTERVAL = Number(arg("interval", 4));
const THINK_TOKENS = Number(arg("think", 400));
const TOOL_STEPS = Number(arg("tools", 0));

const now = () => Date.now() / 1000;

const WORKSPACE = {
  id: "ws-mock",
  path: "D:/mock-workspace",
  name: "mock-workspace",
  created_at: now(),
  updated_at: now(),
};

/** 会话表：engine_session_id -> {item, events} */
const sessions = new Map();

function makeSession(model = "mock-model") {
  const engine = `eng-${Math.random().toString(36).slice(2, 10)}`;
  const item = {
    id: `sess-${Math.random().toString(36).slice(2, 10)}`,
    engine_session_id: engine,
    workspace_id: WORKSPACE.id,
    cwd: WORKSPACE.path,
    title: "新会话",
    model,
    permission_mode: "default",
    turn_count: 0,
    created_at: now(),
    updated_at: now(),
  };
  sessions.set(engine, { item, events: [], seq: 0 });
  return item;
}

const WORDS =
  "流式渲染 增量 解析 缓冲 帧率 主线程 布局 重排 绘制 节流 合并 提交 订阅 选择器 记忆化 分块 稳定 前缀 追加 抖动".split(
    " ",
  );

/** 造一段有 markdown 结构的长答案，逼近真实长输出。 */
function buildAnswer(tokenCount) {
  const chunks = [];
  let i = 0;
  let section = 1;
  while (i < tokenCount) {
    chunks.push(`\n\n## 小节 ${section}\n\n`);
    i += 4;
    for (let p = 0; p < 3 && i < tokenCount; p += 1) {
      const line = [];
      for (let w = 0; w < 40 && i < tokenCount; w += 1, i += 1) {
        line.push(WORDS[(i + w) % WORDS.length]);
      }
      chunks.push(`${line.join("")}。\n\n`);
    }
    if (i < tokenCount) {
      chunks.push("```ts\n");
      for (let l = 0; l < 8 && i < tokenCount; l += 1, i += 2) {
        chunks.push(`const value${l} = compute(${l}); // 第 ${l} 行\n`);
      }
      chunks.push("```\n\n");
      chunks.push(`| 名称 | 说明 |\n| --- | --- |\n`);
      for (let r = 0; r < 4 && i < tokenCount; r += 1, i += 2) {
        chunks.push(`| 项 ${r} | ${WORDS[r % WORDS.length]} |\n`);
      }
    }
    section += 1;
  }
  return chunks.join("");
}

/** 把长文本切成 token 粒度的小片。 */
function toDeltas(text, count) {
  const size = Math.max(1, Math.ceil(text.length / count));
  const out = [];
  for (let at = 0; at < text.length; at += size) out.push(text.slice(at, at + size));
  return out;
}

/** 专门用来验证分块渲染与整篇渲染是否一致：塞满容易被切坏的结构。 */
const TORTURE = `# 标题一

普通段落，带 **加粗**、*斜体*、\`行内代码\` 和 [链接](https://example.com)。

## 紧凑列表

- 第一项
- 第二项
  - 嵌套项
  - 另一个嵌套项
- 第三项

## 松散列表

- 松散第一项

- 松散第二项

- 松散第三项

1. 有序一
2. 有序二

   段落属于第二项。

3. 有序三

## 引用

> 引用第一段
>
> 引用第二段
> 换行继续

## 代码

\`\`\`ts
const a = 1;

const b = 2; // 空行也在围栏里
\`\`\`

~~~python
def f():
    return 1
~~~

缩进代码：

    indented = True
    still = True

## 表格

| 列 A | 列 B |
| --- | ---: |
| 1 | 2 |
| 长一点的单元格 | \`code\` |

***

结尾段落。
`;

const ANSWER = arg("sample", "") === "torture" ? TORTURE : buildAnswer(TOKENS);
const ANSWER_STEPS = arg("sample", "") === "torture" ? Math.min(TOKENS, ANSWER.length) : TOKENS;
const THINKING = buildAnswer(THINK_TOKENS).replace(/```[\s\S]*?```/g, "");

function json(res, body, status = 200) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(data),
  });
  res.end(data);
}

function sseHead(res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
}

function sseSend(res, event, data) {
  res.write(`event: ${event}\ndata: ${data ?? ""}\n\n`);
}

async function readBody(req) {
  const parts = [];
  for await (const chunk of req) parts.push(chunk);
  if (!parts.length) return {};
  try {
    return JSON.parse(Buffer.concat(parts).toString("utf8"));
  } catch {
    return {};
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function streamChat(req, res, body) {
  const engine = body.session_id || makeSession(body.model).engine_session_id;
  const record = sessions.get(engine) ?? sessions.set(engine, { item: makeSession().item, events: [], seq: 0 }).get(engine);
  const push = (type, data) => {
    record.seq += 1;
    record.events.push({ seq: record.seq, type, data, timestamp: Date.now() });
  };

  sseHead(res);
  const startedAt = Date.now();
  push("turn/start", {});
  push("user/message", { content: [{ type: "text", text: String(body.message ?? "") }] });

  let closed = false;
  req.on("close", () => {
    closed = true;
  });

  sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_start", content_block: { type: "thinking" } } }));
  for (const piece of toDeltas(THINKING, THINK_TOKENS)) {
    if (closed) return;
    sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_delta", delta: { type: "thinking_delta", thinking: piece } } }));
    await sleep(INTERVAL);
  }

  // 中间步骤：思考 -> 少量正文 -> 带 tool_use 的整段消息，验证 steps 折叠。
  for (let step = 0; step < TOOL_STEPS; step += 1) {
    if (closed) return;
    sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_start", content_block: { type: "text" } } }));
    for (const piece of toDeltas(`先看一下第 ${step + 1} 处。`, 6)) {
      sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_delta", delta: { type: "text_delta", text: piece } } }));
      await sleep(INTERVAL);
    }
    const stepContent = [
      { type: "text", text: `先看一下第 ${step + 1} 处。` },
      { type: "tool_use", name: "read_file", input: { path: `src/module-${step}.ts` } },
    ];
    sseSend(res, "assistant", JSON.stringify({ type: "assistant", message: { role: "assistant", content: stepContent } }));
    push("assistant/message", { content: stepContent });
    sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_start", content_block: { type: "thinking" } } }));
    for (const piece of toDeltas(`读到了第 ${step + 1} 个文件，继续。`, 8)) {
      sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_delta", delta: { type: "thinking_delta", thinking: piece } } }));
      await sleep(INTERVAL);
    }
  }

  sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_start", content_block: { type: "text" } } }));
  for (const piece of toDeltas(ANSWER, ANSWER_STEPS)) {
    if (closed) return;
    sseSend(res, "stream_event", JSON.stringify({ type: "stream_event", event: { type: "content_block_delta", delta: { type: "text_delta", text: piece } } }));
    await sleep(INTERVAL);
  }

  const content = [
    { type: "thinking", thinking: THINKING },
    { type: "text", text: ANSWER },
  ];
  sseSend(res, "assistant", JSON.stringify({ type: "assistant", message: { role: "assistant", content } }));
  push("assistant/message", { content });
  push("turn/end", {});
  record.item.turn_count += 1;
  record.item.updated_at = now();

  sseSend(
    res,
    "result",
    JSON.stringify({
      type: "result",
      subtype: "success",
      is_error: false,
      duration_ms: Date.now() - startedAt,
      usage: { input_tokens: 1200, output_tokens: TOKENS },
      context_usage: { usage_ratio: 0.2, total_tokens: 1200 + TOKENS, context_window: 200000 },
    }),
  );
  sseSend(res, "done", "");
  res.end();
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
  const path = url.pathname;
  const method = req.method ?? "GET";

  if (path === "/health") return json(res, { status: "ok", sessions: sessions.size });
  if (path === "/app/info")
    return json(res, {
      name: "nono-mock",
      version: "0.0.0",
      engine: "mock",
      host: "127.0.0.1",
      port: PORT,
      cwd: WORKSPACE.path,
      model: "mock-model",
      catalog_ready: true,
      catalog_error: null,
    });
  if (path === "/app/catalog")
    return json(res, {
      adapters: ["mock"],
      default: { provider: "mock", model: "mock-model" },
      providers: [
        {
          id: "mock",
          display_name: "Mock",
          api: "openai",
          base_url: null,
          api_key_env: "MOCK_KEY",
          has_key: true,
          models: [{ id: "mock-model", name: "mock-model", context_window: 200000 }],
        },
      ],
      config_path: null,
    });
  if (path === "/app/mcp")
    return json(res, { config_path: null, enabled: true, servers: [] });
  if (path === "/app/mcp/enabled" && method === "POST") {
    await readBody(req);
    return json(res, { config_path: null, enabled: true, servers: [] });
  }
  if (path === "/app/mcp/servers") {
    await readBody(req);
    return json(res, { config_path: null, enabled: true, servers: [] });
  }
  if (path === "/app/workspaces") {
    if (method === "POST") {
      await readBody(req);
      return json(res, WORKSPACE);
    }
    return json(res, { workspaces: [WORKSPACE] });
  }
  if (path === "/app/permissions") return json(res, { permissions: [] });
  if (path === "/commands") return json(res, { commands: [] });
  if (path === "/app/sessions") {
    if (method === "POST") {
      const body = await readBody(req);
      return json(res, makeSession(body.model));
    }
    return json(res, { sessions: [...sessions.values()].map((item) => item.item) });
  }
  if (path.startsWith("/app/sessions/")) {
    const id = decodeURIComponent(path.slice("/app/sessions/".length));
    const found = [...sessions.values()].find((item) => item.item.id === id);
    if (!found) return json(res, { detail: "not found" }, 404);
    if (method === "PATCH") {
      const body = await readBody(req);
      Object.assign(found.item, body, { updated_at: now() });
    }
    if (method === "DELETE") {
      sessions.delete(found.item.engine_session_id);
      return json(res, { status: "ok" });
    }
    return json(res, found.item);
  }
  if (path === "/chat" && method === "POST") return streamChat(req, res, await readBody(req));
  if (path === "/abort" && method === "POST") {
    await readBody(req);
    return json(res, { status: "aborted" });
  }
  if (path.endsWith("/events")) {
    const engine = decodeURIComponent(path.slice("/session/".length, path.length - "/events".length));
    const record = sessions.get(engine);
    sseHead(res);
    for (const event of record?.events ?? []) sseSend(res, "session_event", JSON.stringify(event));
    sseSend(res, "done", "");
    return res.end();
  }
  if (path.endsWith("/permission") && method === "POST") {
    const body = await readBody(req);
    return json(res, { effective: body.mode ?? "default" });
  }
  return json(res, { detail: `mock: ${method} ${path}` }, 404);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(
    `mock sidecar on http://127.0.0.1:${PORT} tokens=${TOKENS} think=${THINK_TOKENS} interval=${INTERVAL}ms answer=${ANSWER.length}chars`,
  );
});
