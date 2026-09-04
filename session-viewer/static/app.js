/* Nono Agent Session Viewer —— 纯前端解析与渲染（无构建、无依赖）。 */

"use strict";

/* ------------------------------------------------------------------ */
/* 常量与标签                                                          */
/* ------------------------------------------------------------------ */

const EVENT_META = {
  "turn/start":       { label: "turn/start",   cls: "turn",   icon: "▶" },
  "turn/end":         { label: "turn/end",     cls: "turn",   icon: "■" },
  "user/input":       { label: "user/input",   cls: "user",   icon: "⌨" },
  "user/message":     { label: "user/message", cls: "user",   icon: "👤" },
  "memory/retrieve":  { label: "memory/retrieve", cls: "memory", icon: "🧠" },
  "memory/write":     { label: "memory/write", cls: "memory", icon: "🧠" },
  "step/start":       { label: "step/start",   cls: "step",   icon: "▸" },
  "step/end":         { label: "step/end",     cls: "step",   icon: "▸" },
  "request/header":   { label: "request/header", cls: "header", icon: "⚙" },
  "assistant/chunk":  { label: "assistant/chunk", cls: "assistant", icon: "◔" },
  "assistant/message":{ label: "assistant/message", cls: "assistant", icon: "🤖" },
  "tool/call":        { label: "tool/call",    cls: "tool",   icon: "🛠" },
  "tool/result":      { label: "tool/result",  cls: "tool",   icon: "↳" },
  "usage":            { label: "usage",        cls: "usage",  icon: "Σ" },
  "compact/boundary": { label: "compact/boundary", cls: "header", icon: "⤓" },
  "api/retry":        { label: "api/retry",    cls: "usage",  icon: "⟳" },
  "permission/denial":{ label: "permission/denial", cls: "tool", icon: "⛔" },
  "permission/onchange":{ label: "permission/onchange", cls: "tool", icon: "⟳" },
  "permission/changed":{ label: "permission/changed", cls: "tool", icon: "✓" },
};

function meta(type) {
  return EVENT_META[type] || { label: type, cls: "step", icon: "•" };
}

/* ------------------------------------------------------------------ */
/* DOM 小工具（只用 textContent，避免会话内容被当作 HTML 注入）         */
/* ------------------------------------------------------------------ */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function badge(type, text) {
  const m = meta(type);
  const b = el("span", "badge " + m.cls, (text != null ? text : m.label));
  b.title = type;
  return b;
}

/* ------------------------------------------------------------------ */
/* 解析与分组                                                          */
/* ------------------------------------------------------------------ */

function parseJsonl(text) {
  const events = [];
  const lines = text.split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    try {
      const obj = JSON.parse(line);
      if (obj && typeof obj === "object" && "seq" in obj && "data" in obj) {
        events.push(obj);
      }
    } catch (_) { /* 跳过残行 */ }
  }
  events.sort((a, b) => (a.seq || 0) - (b.seq || 0));
  return events;
}

function buildModel(events, sessionId, header) {
  const turns = [];
  let curTurn = null;
  let curStep = null;
  let turnStartTs = null;

  for (const ev of events) {
    switch (ev.type) {
      case "turn/start":
        curTurn = { turn: ev.data.turn, start: ev, end: null, steps: [], preEvents: [] };
        turns.push(curTurn);
        curStep = null;
        turnStartTs = ev.timestamp;
        break;
      case "step/start":
        curStep = {
          step: ev.data.step,
          turn: ev.data.turn,
          model: ev.data.model,
          start: ev,
          end: null,
          events: [],
        };
        if (curTurn) curTurn.steps.push(curStep);
        break;
      case "step/end":
        if (curStep) curStep.end = ev;
        break;
      case "turn/end":
        if (curTurn) curTurn.end = ev;
        curStep = null;
        break;
    }

    if (curStep) curStep.events.push(ev);
    else if (curTurn) curTurn.preEvents.push(ev);
  }

  // 给每个事件算相对 turn 开始的时间偏移（ms）
  for (const turn of turns) {
    const base = turn.start && turn.start.timestamp;
    const mark = (ev) => { if (ev && base != null && ev.timestamp != null) ev._dur = ev.timestamp - base; };
    mark(turn.start);
    for (const ev of turn.preEvents) mark(ev);
    for (const st of turn.steps) {
      mark(st.start); mark(st.end);
      for (const ev of st.events) mark(ev);
    }
    mark(turn.end);
  }

  return { sessionId, header, events, turns, summary: summarize(events, turns) };
}

function summarize(events, turns) {
  const usage = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 };
  let toolCalls = 0;
  let toolResults = 0;
  let steps = 0;
  for (const ev of events) {
    if (ev.type === "usage" && ev.data && ev.data.usage) {
      const u = ev.data.usage;
      usage.input_tokens += u.input_tokens || 0;
      usage.output_tokens += u.output_tokens || 0;
      usage.cache_read_input_tokens += u.cache_read_input_tokens || 0;
      usage.cache_creation_input_tokens += u.cache_creation_input_tokens || 0;
    } else if (ev.type === "tool/call") toolCalls++;
    else if (ev.type === "tool/result") toolResults++;
    else if (ev.type === "step/start") steps++;
  }
  usage.total_tokens = usage.input_tokens + usage.output_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens;
  const first = events[0];
  const last = events[events.length - 1];
  const wall = (first && last && first.timestamp != null && last.timestamp != null)
    ? (last.timestamp - first.timestamp) : null;
  return { turns: turns.length, steps, toolCalls, toolResults, usage, wallMs: wall };
}

/* ------------------------------------------------------------------ */
/* 渲染辅助                                                            */
/* ------------------------------------------------------------------ */

function fmtMs(ms) {
  if (ms == null) return "";
  if (ms < 1000) return ms.toFixed(0) + "ms";
  if (ms < 60000) return (ms / 1000).toFixed(1) + "s";
  const m = Math.floor(ms / 60000);
  const s = ((ms % 60000) / 1000).toFixed(1);
  return m + "m " + s + "s";
}

function fmtTs(ts) {
  if (ts == null) return "";
  return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
}

function previewFor(ev) {
  const d = ev.data || {};
  switch (ev.type) {
    case "user/input":
    case "user/message":
      return typeof d.content === "string" ? d.content : (Array.isArray(d.content) ? blockSummary(d.content) : "");
    case "assistant/message":
      return Array.isArray(d.content) ? blockSummary(d.content) : "";
    case "tool/call":
      return d.name || "";
    case "tool/result":
      return typeof d.result === "string" ? truncate(d.result, 200) : (d.is_error ? "[error]" : "[result]");
    case "usage":
      return usageText(d.usage);
    case "request/header":
      return "model=" + ((d.header && d.header.config && d.header.config.model) || "?");
    case "memory/retrieve":
      return "query=" + truncate(String(d.query || ""), 60) + (d.adopted ? " (hit)" : " (miss)");
    case "memory/write":
      return (d.kind || "") + " " + truncate(d.memory_id || "", 30);
    case "step/start":
      return "model=" + (d.model || "?");
    case "step/end":
    case "turn/end":
      return "reason=" + (d.reason || "?");
    case "api/retry":
      return "reason=" + (d.reason || "?");
    case "permission/denial":
      return "denied " + (d.tool || "");
    case "permission/onchange":
      return (d.from || "?") + " → " + (d.to || "?");
    case "permission/changed":
      return (d.from || "?") + " → " + (d.to || "?") + (d.at ? " @" + d.at : "");
    case "compact/boundary":
      return (d.metadata && d.metadata.strategy) || "";
    default:
      return "";
  }
}

function truncate(s, n) {
  s = String(s);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function blockSummary(content) {
  const parts = [];
  for (const b of content) {
    if (!b || typeof b !== "object") continue;
    if (b.type === "text") parts.push(truncate(String(b.text || ""), 80));
    else if (b.type === "thinking") parts.push("[reasoning]");
    else if (b.type === "tool_use") parts.push("🛠 " + (b.name || "?"));
    else parts.push("[" + (b.type || "?") + "]");
  }
  return parts.join("  ");
}

function usageText(u) {
  if (!u) return "";
  const total = (u.input_tokens || 0) + (u.output_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
  return `in=${u.input_tokens || 0} out=${u.output_tokens || 0} cache_r=${u.cache_read_input_tokens || 0} cache_c=${u.cache_creation_input_tokens || 0} (${total})`;
}

/* ------------------------------------------------------------------ */
/* 渲染：摘要                                                          */
/* ------------------------------------------------------------------ */

function renderSummary(model) {
  const box = document.getElementById("summary");
  box.innerHTML = "";
  box.classList.remove("hidden");
  const s = model.summary;

  const kv = (label, value, sub) => {
    const k = el("div", "kv");
    k.appendChild(el("b", null, value));
    k.appendChild(el("span", null, label + (sub ? " · " + sub : "")));
    return k;
  };

  let title = model.sessionId || "(offline file)";
  if (model.header && model.header.cwd) title += "  ·  cwd=" + model.header.cwd;
  const headline = (model.header && model.header.summary)
    ? model.header.summary
    : (s.turns + " 轮 / " + s.steps + " 步");
  box.appendChild(kv(title, headline, s.turns + " 轮 / " + s.steps + " 步"));
  box.appendChild(kv("工具调用", s.toolCalls + " / " + s.toolResults, ""));
  box.appendChild(kv("累计 token", s.usage.total_tokens, "in=" + s.usage.input_tokens + " out=" + s.usage.output_tokens + " cache_r=" + s.usage.cache_read_input_tokens + " cache_c=" + s.usage.cache_creation_input_tokens));
  if (s.wallMs != null) box.appendChild(kv("会话时长", fmtMs(s.wallMs), ""));
}

/* ------------------------------------------------------------------ */
/* 渲染：轨迹 Timeline                                                 */
/* ------------------------------------------------------------------ */

function renderTrajectory(model) {
  const root = document.getElementById("trajectory");
  root.innerHTML = "";
  if (!model.turns.length) {
    root.appendChild(el("div", "empty", "没有可渲染的会话事件。"));
    return;
  }
  for (const turn of model.turns) {
    root.appendChild(renderTurn(turn));
  }
}

function renderTurn(turn) {
  const node = el("section", "turn");
  const head = el("div", "head");
  head.appendChild(el("span", "caret", "▾"));
  head.appendChild(badge("turn/start"));
  head.appendChild(el("span", "title", "Turn " + turn.turn));
  if (turn.end && turn.end.data && turn.end.data.reason) {
    head.appendChild(el("span", "meta", "· reason=" + turn.end.data.reason));
  }
  if (turn.end && turn.end._dur != null) head.appendChild(el("span", "meta", "· " + fmtMs(turn.end._dur)));
  node.appendChild(head);

  const body = el("div", "turn-body");
  for (const ev of turn.preEvents) {
    if (ev.type === "turn/start") continue;
    body.appendChild(renderEventRow(ev, turn));
  }
  for (const st of turn.steps) {
    body.appendChild(renderStep(st, turn));
  }
  node.appendChild(body);
  bindCollapse(head, body);
  return node;
}

function renderStep(st, turn) {
  const node = el("section", "step");
  const head = el("div", "head");
  head.appendChild(el("span", "caret", "▾"));
  head.appendChild(badge("step/start"));
  head.appendChild(el("span", "title", "Step " + st.step));
  head.appendChild(el("span", "meta", "· " + (st.model || "?")));
  if (st.end && st.end.data && st.end.data.reason) head.appendChild(el("span", "meta", "· reason=" + st.end.data.reason));
  if (st.start && st.end && st.end._dur != null) head.appendChild(el("span", "meta", "· " + fmtMs(st.end._dur)));
  node.appendChild(head);

  const body = el("div", "step-body");

  // 聚合 assistant/chunk，其余事件逐条渲染
  const chunks = st.events.filter(e => e.type === "assistant/chunk");
  for (const ev of st.events) {
    if (ev.type === "assistant/chunk") continue;
    if (ev.type === "step/start") continue;
    body.appendChild(renderEventRow(ev, turn));
  }
  if (chunks.length) {
    body.appendChild(renderChunkSummary(chunks, turn));
  }
  node.appendChild(body);
  bindCollapse(head, body);
  return node;
}

function renderChunkSummary(chunks, turn) {
  const row = el("div", "event");
  const r = el("div", "row");
  r.appendChild(badge("assistant/chunk", "chunks × " + chunks.length));
  r.appendChild(el("span", "ts", fmtMs(chunks[0]._dur) + " → " + fmtMs(chunks[chunks.length - 1]._dur)));
  r.appendChild(el("span", "preview", "流式增量（点击查看详情）"));
  row.appendChild(r);
  row.addEventListener("click", () => openInspector({ type: "assistant/chunk (aggregated)", data: { count: chunks.length, chunks } }));
  return row;
}

function renderEventRow(ev, turn) {
  const row = el("div", "event");
  const r = el("div", "row");
  r.appendChild(badge(ev.type));
  if (ev._dur != null) r.appendChild(el("span", "ts", "+" + fmtMs(ev._dur)));
  const preview = previewFor(ev);
  if (preview) r.appendChild(el("span", "preview", preview));
  row.appendChild(r);

  // 内容型事件直接内联展开（放在标题行下方）
  if (ev.type === "assistant/message" && Array.isArray(ev.data.content)) {
    const wrap = el("div", "blocks");
    for (const b of ev.data.content) wrap.appendChild(renderContentBlock(b));
    row.appendChild(wrap);
  } else if (ev.type === "request/header") {
    row.appendChild(renderHeaderBlock(ev.data.header));
  } else if (ev.type === "tool/call") {
    row.appendChild(renderToolCallBlock(ev.data));
  } else if (ev.type === "tool/result") {
    row.appendChild(renderToolResultBlock(ev.data));
  } else if (ev.type === "usage") {
    row.appendChild(renderUsageBlock(ev.data));
  }

  row.addEventListener("click", (e) => {
    // 点击内联展开区域里的元素不弹详情面板
    if (e.target.closest(".block, details")) return;
    openInspector(ev);
  });
  return row;
}

function renderContentBlock(b) {
  if (!b || typeof b !== "object") return el("div", "block", String(b));
  if (b.type === "thinking") {
    const d = el("details", "block thinking");
    const summary = el("summary");
    summary.appendChild(el("span", null, "🧠 思考 reasoning"));
    d.appendChild(summary);
    d.appendChild(el("pre", null, b.thinking || ""));
    return d;
  }
  if (b.type === "redacted_thinking") {
    return el("div", "block thinking", "[redacted thinking]");
  }
  if (b.type === "tool_use") {
    const d = el("div", "block tool");
    d.appendChild(el("strong", null, "🛠 " + (b.name || "?")));
    d.appendChild(el("div", "args", JSON.stringify(b.input ?? {}, null, 2)));
    return d;
  }
  // text 及其他
  return el("div", "block text", typeof b.text === "string" ? b.text : JSON.stringify(b));
}

function renderHeaderBlock(header) {
  const d = el("div", "block header");
  if (!header) { d.appendChild(el("span", null, "(empty)")); return d; }
  const cfg = header.config || {};
  const table = el("table", "kv-table");
  const add = (k, v) => { const tr = el("tr"); tr.appendChild(el("td", null, k)); tr.appendChild(el("td", null, String(v ?? ""))); table.appendChild(tr); };
  add("provider", cfg.provider);
  add("protocol", cfg.protocol);
  add("model", cfg.model);
  add("context_size", cfg.context_size);
  add("agent_id", cfg.agent_id);
  d.appendChild(table);

  if (header.system) {
    const sys = el("details", "block");
    const s = el("summary"); s.textContent = "system prompt (" + header.system.length + " chars)";
    sys.appendChild(s);
    sys.appendChild(el("pre", "system-prompt", header.system));
    d.appendChild(sys);
  }
  if (Array.isArray(header.tools) && header.tools.length) {
    const tools = el("details", "block");
    const s = el("summary"); s.textContent = "tools (" + header.tools.length + ")";
    tools.appendChild(s);
    const ul = el("ul");
    for (const t of header.tools) ul.appendChild(el("li", null, t.name || t.function || "?"));
    tools.appendChild(ul);
    d.appendChild(tools);
  }
  return d;
}

function renderToolCallBlock(data) {
  const d = el("div", "block tool");
  d.appendChild(el("strong", null, "call " + (data.call_id || "") + " → " + (data.name || "?")));
  d.appendChild(el("div", "args", JSON.stringify(data.args ?? {}, null, 2)));
  return d;
}

function renderToolResultBlock(data) {
  const d = el("div", "block result" + (data.is_error ? " is-error" : ""));
  d.appendChild(el("strong", null, (data.is_error ? "✗ " : "✓ ") + (data.call_id || "")));
  d.appendChild(el("div", "text", typeof data.result === "string" ? data.result : JSON.stringify(data.result)));
  return d;
}

function renderUsageBlock(data) {
  const d = el("div", "block");
  const u = data.usage || {};
  const total = (u.input_tokens || 0) + (u.output_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
  d.appendChild(el("span", null, usageText(u)));
  if (data.model) d.appendChild(el("span", "meta", "  · model=" + data.model));
  return d;
}

function bindCollapse(head, body) {
  head.addEventListener("click", (e) => {
    if (e.target.closest(".event, details, .block")) return;
    body.classList.toggle("hidden");
    head.querySelector(".caret").textContent = body.classList.contains("hidden") ? "▸" : "▾";
  });
}

/* ------------------------------------------------------------------ */
/* 渲染：对话 Chat                                                     */
/* ------------------------------------------------------------------ */

function renderConversation(model) {
  const root = document.getElementById("conversation");
  root.innerHTML = "";
  if (!model.turns.length) {
    root.appendChild(el("div", "empty", "没有可渲染的会话。"));
    return;
  }
  for (const turn of model.turns) {
    root.appendChild(el("div", "turn-sep", "Turn " + turn.turn + (turn.end && turn.end.data.reason ? " · " + turn.end.data.reason : "")));
    // 回合开头原始输入（仅展示，若有与 user/message 不同）
    for (const st of turn.steps) {
      root.appendChild(el("div", "step-sep", "Step " + st.step + " · " + (st.model || "?")));
      renderStepConversation(root, st);
    }
  }
}

function renderStepConversation(root, st) {
  const byType = {};
  for (const ev of st.events) {
    (byType[ev.type] = byType[ev.type] || []).push(ev);
  }
  // 用户消息
  for (const u of (byType["user/message"] || [])) {
    root.appendChild(msgBubble("user", "👤 用户", contentText(u.data && u.data.content)));
  }
  // assistant 消息
  for (const a of (byType["assistant/message"] || [])) {
    const bubble = el("div", "bubble");
    const content = a.data && a.data.content;
    if (Array.isArray(content)) {
      for (const b of content) bubble.appendChild(renderContentBlock(b));
    } else {
      bubble.appendChild(el("div", "text", typeof content === "string" ? content : ""));
    }
    root.appendChild(msgBubble("assistant", "🤖 助手", null, bubble));
  }
  // 工具结果（按 call_id 对齐 tool/call）
  const calls = {};
  for (const c of (byType["tool/call"] || [])) calls[c.data.call_id] = c;
  for (const r of (byType["tool/result"] || [])) {
    const call = calls[r.data.call_id];
    const bubble = el("div", "bubble");
    if (call) {
      bubble.appendChild(el("strong", null, "🛠 " + (call.data.name || "?") + "  " + (call.data.call_id || "")));
    } else {
      bubble.appendChild(el("strong", null, "🛠 tool result"));
    }
    bubble.appendChild(el("div", "text", typeof r.data.result === "string" ? r.data.result : JSON.stringify(r.data.result)));
    if (r.data.is_error) bubble.classList.add("is-error");
    root.appendChild(msgBubble("tool", "↳ 工具", null, bubble));
  }
  // usage 脚注
  const usage = (byType["usage"] || [])[0];
  if (usage) {
    root.appendChild(el("div", "step-sep", usageText(usage.data.usage) + (usage.data.model ? " · " + usage.data.model : "")));
  }
}

function contentText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map(b => (b && b.type === "text") ? b.text : "").filter(Boolean).join("\n");
  }
  return "";
}

function msgBubble(cls, who, text, bubbleNode) {
  const m = el("div", "msg " + cls);
  m.appendChild(el("div", "who", who));
  const bubble = bubbleNode || el("div", "bubble");
  if (text !== null && text !== undefined) bubble.appendChild(el("div", "text", text));
  m.appendChild(bubble);
  return m;
}

/* ------------------------------------------------------------------ */
/* Inspector                                                           */
/* ------------------------------------------------------------------ */

function openInspector(ev) {
  const panel = document.getElementById("inspector");
  const title = document.getElementById("inspector-title");
  const body = document.getElementById("inspector-body");
  panel.classList.remove("hidden");
  title.textContent = (ev.seq != null ? "#" + ev.seq + " " : "") + ev.type;
  const payload = { type: ev.type, seq: ev.seq, timestamp: ev.timestamp, _dur: ev._dur, data: ev.data };
  body.textContent = JSON.stringify(payload, null, 2);
}

/* ------------------------------------------------------------------ */
/* 加载与入口                                                          */
/* ------------------------------------------------------------------ */

let currentModel = null;

function renderAll(model) {
  currentModel = model;
  renderSummary(model);
  renderTrajectory(model);
  renderConversation(model);
}

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.add("hidden"), 4000);
}

function loadFromText(text, sessionId, header) {
  const events = parseJsonl(text);
  if (!events.length) { showToast("没有解析到任何会话事件（不是有效的 JSONL 会话文件？）"); return; }
  renderAll(buildModel(events, sessionId, header));
}

async function loadSessionById() {
  const id = document.getElementById("session-id").value.trim();
  if (!id) { showToast("请输入 session_id"); return; }
  try {
    const resp = await fetch("/api/session/" + encodeURIComponent(id));
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const data = await resp.json();
    renderAll(buildModel(data.events, data.session_id, data.header));
    document.getElementById("session-id").value = data.session_id;
  } catch (err) {
    showToast("加载失败：" + err.message);
  }
}

async function loadSessionList() {
  try {
    const resp = await fetch("/api/sessions");
    if (!resp.ok) throw new Error(resp.statusText);
    const list = await resp.json();
    if (!list.length) { showToast("没有找到本地会话（~/.nono-agent/sessions/ 为空）"); return; }
    openInspector({ type: "sessions", data: list });
    document.getElementById("inspector-title").textContent = "本地会话列表（点击左侧“加载会话”并粘贴 id）";
  } catch (err) {
    showToast("获取会话列表失败：" + err.message);
  }
}

function handleFile(file) {
  const reader = new FileReader();
  reader.onload = () => loadFromText(String(reader.result), null, null);
  reader.onerror = () => showToast("读取文件失败");
  reader.readAsText(file);
}

/* 拖拽载入离线文件 */
function setupDrop() {
  const hint = document.getElementById("drop-hint");
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) {
      depth++; hint.classList.remove("hidden");
    }
  });
  window.addEventListener("dragleave", (e) => {
    e.preventDefault();
    depth = Math.max(0, depth - 1);
    if (!depth) hint.classList.add("hidden");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0; hint.classList.add("hidden");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) handleFile(f);
  });
}

function setup() {
  document.getElementById("load-session").addEventListener("click", loadSessionById);
  document.getElementById("session-id").addEventListener("keydown", (e) => { if (e.key === "Enter") loadSessionById(); });
  document.getElementById("open-file").addEventListener("click", () => document.getElementById("file-input").click());
  document.getElementById("file-input").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) handleFile(e.target.files[0]);
    e.target.value = "";
  });
  document.getElementById("list-sessions").addEventListener("click", loadSessionList);
  document.getElementById("inspector-close").addEventListener("click", () => document.getElementById("inspector").classList.add("hidden"));

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById(tab.dataset.view).classList.add("active");
    });
  });

  setupDrop();

  // 支持 ?session=<id> 深链：打开页面即自动加载该会话
  const params = new URLSearchParams(window.location.search);
  const sid = params.get("session") || params.get("sid");
  if (sid) {
    document.getElementById("session-id").value = sid;
    loadSessionById();
  }
}

document.addEventListener("DOMContentLoaded", setup);
