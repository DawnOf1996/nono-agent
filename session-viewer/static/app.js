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

    // turn/start、turn/end 挂在 turn 对象上，按时间线在步骤前/后单独渲染；
    // 否则 turn/end 会掉进 preEvents，标签出现在 step 前面。
    if (ev.type === "turn/start" || ev.type === "turn/end") continue;
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
  // 会话用时 = 各轮 turn/start → turn/end 的差值合计（不含回合之间的空闲）
  let durationMs = 0;
  let completedTurns = 0;
  for (const turn of turns) {
    const start = turn.start && turn.start.timestamp;
    const end = turn.end && turn.end.timestamp;
    if (start != null && end != null) {
      durationMs += end - start;
      completedTurns++;
    }
  }
  return {
    turns: turns.length,
    steps,
    toolCalls,
    toolResults,
    usage,
    wallMs: completedTurns ? durationMs : null,
  };
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
  if (s.wallMs != null) box.appendChild(kv("会话用时", fmtMs(s.wallMs), ""));
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
  markLocate(head, "turn/start", turn.start && turn.start.seq);
  node.appendChild(head);

  const body = el("div", "turn-body");
  for (const ev of turn.preEvents) {
    if (ev.type === "turn/start" || ev.type === "turn/end") continue;
    body.appendChild(renderEventRow(ev, turn));
  }
  for (const st of turn.steps) {
    body.appendChild(renderStep(st, turn));
  }
  if (turn.end) body.appendChild(renderEventRow(turn.end, turn));
  node.appendChild(body);
  bindCollapse(head, body, true);
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
  markLocate(head, "step/start", st.start && st.start.seq);
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
  bindCollapse(head, body, true);
  return node;
}

function renderChunkSummary(chunks, turn) {
  const row = el("div", "event");
  const r = el("div", "row");
  r.appendChild(badge("assistant/chunk", "chunks × " + chunks.length));
  r.appendChild(el("span", "ts", fmtMs(chunks[0]._dur) + " → " + fmtMs(chunks[chunks.length - 1]._dur)));
  r.appendChild(el("span", "preview", "流式增量（点击查看详情）"));
  row.appendChild(r);
  markLocate(row, "assistant/chunk", chunks[0] && chunks[0].seq);
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
  markLocate(row, ev.type, ev.seq);

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

function markLocate(node, type, seq) {
  if (!node || !type) return;
  node.dataset.eventType = type;
  if (seq != null) node.dataset.seq = String(seq);
}

function setSectionCollapsed(section, collapsed) {
  const body = section.querySelector(":scope > .turn-body, :scope > .step-body");
  const caret = section.querySelector(":scope > .head .caret");
  if (!body) return;
  body.classList.toggle("hidden", collapsed);
  if (caret) caret.textContent = collapsed ? "▸" : "▾";
}

function trajectoryBodies() {
  return Array.from(document.querySelectorAll("#trajectory .turn-body, #trajectory .step-body"));
}

function isFullyExpanded() {
  const bodies = trajectoryBodies();
  return bodies.length > 0 && bodies.every((b) => !b.classList.contains("hidden"));
}

function setAllCollapsed(collapsed) {
  document.querySelectorAll("#trajectory section.turn, #trajectory section.step").forEach((s) => {
    setSectionCollapsed(s, collapsed);
  });
  updateFoldButton();
}

function toggleAllFold() {
  setAllCollapsed(isFullyExpanded());
}

function updateFoldButton() {
  const btn = document.getElementById("fold-all");
  if (!btn) return;
  const expanded = isFullyExpanded();
  btn.textContent = expanded ? "全部折叠" : "全部展开";
  btn.title = expanded ? "收起所有 Turn / Step" : "展开所有 Turn / Step";
}

function bindCollapse(head, body, collapsed) {
  if (collapsed) {
    body.classList.add("hidden");
    const caret = head.querySelector(".caret");
    if (caret) caret.textContent = "▸";
  }
  head.addEventListener("click", (e) => {
    if (e.target.closest(".event, details, .block")) return;
    body.classList.toggle("hidden");
    head.querySelector(".caret").textContent = body.classList.contains("hidden") ? "▸" : "▾";
    updateFoldButton();
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

function setInspector(titleText, contentNode) {
  const panel = document.getElementById("inspector");
  const title = document.getElementById("inspector-title");
  const body = document.getElementById("inspector-body");
  panel.classList.remove("hidden");
  title.textContent = titleText;
  body.innerHTML = "";
  body.appendChild(contentNode);
}

function openInspector(ev) {
  const payload = { type: ev.type, seq: ev.seq, timestamp: ev.timestamp, _dur: ev._dur, data: ev.data };
  const pre = el("pre");
  pre.textContent = JSON.stringify(payload, null, 2);
  setInspector((ev.seq != null ? "#" + ev.seq + " " : "") + ev.type, pre);
}

function fmtMtime(ts) {
  if (ts == null) return "";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function renderSessionList(list) {
  const wrap = el("div", "session-list");
  const currentId = document.getElementById("session-id").value.trim();
  for (const item of list) {
    const sid = item.session_id || "";
    const row = el("div", "session-item" + (sid === currentId ? " selected" : ""));
    row.title = "点击填入 session_id";
    row.appendChild(el("div", "sid", sid));
    const bits = [];
    if (item.event_count != null) bits.push(item.event_count + " 事件");
    if (item.last_modified != null) bits.push(fmtMtime(item.last_modified));
    const header = item.header || {};
    if (header.summary) bits.push(header.summary);
    else if (header.cwd) bits.push(header.cwd);
    if (bits.length) row.appendChild(el("div", "meta", bits.join(" · ")));
    row.addEventListener("click", () => {
      const input = document.getElementById("session-id");
      input.value = sid;
      input.focus();
      input.select();
      wrap.querySelectorAll(".session-item").forEach((n) => n.classList.remove("selected"));
      row.classList.add("selected");
      showToast("已填入 session_id，点击「加载会话」打开");
    });
    wrap.appendChild(row);
  }
  setInspector("本地会话列表（点击填入 session_id）", wrap);
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
  setupLocateBar();
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
    renderSessionList(list);
  } catch (err) {
    showToast("获取会话列表失败：" + err.message);
  }
}

const ZSTD_MAGIC = [0x28, 0xb5, 0x2f, 0xfd];

function looksLikeZstd(buffer, filename) {
  if (filename && /\.jsonl\.zst$/i.test(filename)) return true;
  if (!buffer || buffer.byteLength < 4) return false;
  const bytes = new Uint8Array(buffer, 0, 4);
  return bytes[0] === ZSTD_MAGIC[0] && bytes[1] === ZSTD_MAGIC[1]
    && bytes[2] === ZSTD_MAGIC[2] && bytes[3] === ZSTD_MAGIC[3];
}

function handleFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    const buffer = reader.result;
    if (looksLikeZstd(buffer, file.name)) {
      showToast("压缩会话（.jsonl.zst）请用上方 session_id 从服务端加载；离线拖入仅支持明文 JSONL。");
      return;
    }
    const text = new TextDecoder("utf-8").decode(buffer);
    loadFromText(text, null, null);
  };
  reader.onerror = () => showToast("读取文件失败");
  reader.readAsArrayBuffer(file);
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

/* ------------------------------------------------------------------ */
/* 按事件类型定位                                                      */
/* ------------------------------------------------------------------ */

const locateState = { type: "", matches: [], index: 0 };

function setupLocateBar() {
  const bar = document.getElementById("locate-bar");
  const select = document.getElementById("locate-type");
  const root = document.getElementById("trajectory");
  const nodes = Array.from(root.querySelectorAll("[data-event-type]"));
  if (!nodes.length) {
    bar.classList.add("hidden");
    locateState.matches = [];
    locateState.index = 0;
    updateLocateStatus();
    return;
  }
  bar.classList.remove("hidden");

  const counts = {};
  for (const n of nodes) {
    const t = n.dataset.eventType;
    counts[t] = (counts[t] || 0) + 1;
  }
  // 按事件流里首次出现的顺序排标签，避免 EVENT_META 把 turn/end 提前到 step 前面
  const ordered = [];
  const seen = new Set();
  const stream = (currentModel && currentModel.events) || [];
  for (const ev of stream) {
    const t = ev && ev.type;
    if (!t || seen.has(t) || !counts[t]) continue;
    seen.add(t);
    ordered.push(t);
  }
  for (const t of Object.keys(counts)) {
    if (!seen.has(t)) ordered.push(t);
  }

  select.innerHTML = "";
  const placeholder = el("option");
  placeholder.value = "";
  placeholder.textContent = "选择事件类型…";
  select.appendChild(placeholder);
  for (const t of ordered) {
    const opt = el("option");
    opt.value = t;
    opt.textContent = t + "  (" + counts[t] + ")";
    select.appendChild(opt);
  }
  // 新会话默认不选中，由用户选择类型后再跳到该类型的第一个节点
  select.value = "";
  rebuildLocateMatches(false);
  updateFoldButton();
}

function rebuildLocateMatches(scrollToFirst) {
  const select = document.getElementById("locate-type");
  const type = select.value;
  const root = document.getElementById("trajectory");
  locateState.type = type;
  locateState.matches = type
    ? Array.from(root.querySelectorAll("[data-event-type]")).filter((n) => n.dataset.eventType === type)
    : [];
  locateState.index = 0;
  updateLocateStatus();
  if (scrollToFirst && locateState.matches.length) locateTo(0);
}

function updateLocateStatus() {
  const status = document.getElementById("locate-status");
  const n = locateState.matches.length;
  status.textContent = n ? (locateState.index + 1) + " / " + n : "0 / 0";
  const disabled = n === 0;
  document.getElementById("locate-prev").disabled = disabled;
  document.getElementById("locate-next").disabled = disabled;
}

function activateTrajectoryTab() {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "trajectory"));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "trajectory"));
}

function expandAncestors(node) {
  let p = node.parentElement;
  while (p && p.id !== "trajectory") {
    if (p.classList.contains("turn") || p.classList.contains("step")) {
      const head = p.querySelector(":scope > .head");
      if (head !== node && !(head && head.contains(node))) {
        setSectionCollapsed(p, false);
      }
    }
    p = p.parentElement;
  }
  updateFoldButton();
}

function highlightLocate(node) {
  document.querySelectorAll(".locate-target").forEach((n) => n.classList.remove("locate-target"));
  void node.offsetWidth;
  node.classList.add("locate-target");
}

function locateTo(index) {
  const n = locateState.matches.length;
  if (!n) return;
  locateState.index = ((index % n) + n) % n;
  const node = locateState.matches[locateState.index];
  activateTrajectoryTab();
  expandAncestors(node);
  updateLocateStatus();
  const afterLayout = () => {
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    highlightLocate(node);
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(afterLayout);
  else afterLayout();
}

function locatePrev() { locateTo(locateState.index - 1); }
function locateNext() { locateTo(locateState.index + 1); }

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
  document.getElementById("fold-all").addEventListener("click", toggleAllFold);
  document.getElementById("locate-type").addEventListener("change", () => rebuildLocateMatches(true));
  document.getElementById("locate-prev").addEventListener("click", locatePrev);
  document.getElementById("locate-next").addEventListener("click", locateNext);

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

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
else setup();
