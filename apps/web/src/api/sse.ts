import type { FileDiffUi } from "@nono/protocol";

import { usageTotal, type TurnTokens } from "@/lib/usage";

export type SseFrame = {
  event: string;
  data: string;
};

export async function* parseSse(response: Response): AsyncGenerator<SseFrame> {
  if (!response.body) {
    throw new Error("SSE response has no body");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let split = buffer.indexOf("\n\n");
    while (split >= 0) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      yield parseFrame(raw);
      split = buffer.indexOf("\n\n");
    }
  }
  if (buffer.trim()) {
    yield parseFrame(buffer);
  }
}

function parseFrame(raw: string): SseFrame {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return { event, data: data.join("\n") };
}

type StreamInner = {
  type?: string;
  delta?: { type?: string; text?: string; thinking?: string };
  content_block?: { type?: string; text?: string; thinking?: string };
};

function streamInner(payload: unknown): StreamInner | null {
  if (!payload || typeof payload !== "object") return null;
  const event = (payload as { event?: StreamInner }).event;
  return event && typeof event === "object" ? event : null;
}

export function extractTextDelta(payload: unknown): string {
  const event = streamInner(payload);
  if (event?.type === "content_block_delta" && event.delta?.type === "text_delta") {
    return event.delta.text ?? "";
  }
  return "";
}

export function extractThinkingDelta(payload: unknown): string {
  const event = streamInner(payload);
  if (event?.type === "content_block_delta" && event.delta?.type === "thinking_delta") {
    return event.delta.thinking ?? "";
  }
  return "";
}

export function isThinkingBlockStart(payload: unknown): boolean {
  const event = streamInner(payload);
  return event?.type === "content_block_start" && event.content_block?.type === "thinking";
}

export function isTextBlockStart(payload: unknown): boolean {
  const event = streamInner(payload);
  return event?.type === "content_block_start" && event.content_block?.type === "text";
}

export function extractAssistantText(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const message = (payload as { message?: { content?: unknown } }).message;
  return contentToText(message?.content);
}

export function extractAssistantThinking(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const message = (payload as { message?: { content?: unknown } }).message;
  return contentToThinking(message?.content);
}

export type ReplayToolCall = {
  name: string;
  input?: Record<string, unknown>;
  /** tool_use 块的 id —— 与 tool_result 的 tool_use_id 对应。 */
  id?: string;
  /** 工具结果文本（tool_result 的 content 展平后）。运行中/未落定为 undefined。 */
  output?: string;
  /** 工具结果是否为错误。 */
  isError?: boolean;
  /** 后端算好的 UI 附件（文件差异等）。只给界面，不进模型上下文。 */
  ui?: FileDiffUi;
};

export type ReplayStep = {
  id: string;
  text?: string;
  thinking?: string;
  tools?: ReplayToolCall[];
};

export function extractTools(payload: unknown): ReplayToolCall[] {
  if (!payload || typeof payload !== "object") return [];
  const message = (payload as { message?: { content?: unknown } }).message;
  return toolsFromContent(message?.content);
}

export function contentToText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!block || typeof block !== "object") return "";
      const rec = block as { type?: string; text?: unknown };
      if (rec.type === "thinking" || rec.type === "tool_use") return "";
      return typeof rec.text === "string" ? rec.text : "";
    })
    .filter(Boolean)
    .join("\n");
}

export type ReplayImage = {
  src: string;
};

export function contentToImages(content: unknown): ReplayImage[] {
  if (!Array.isArray(content)) return [];
  const images: ReplayImage[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const rec = block as {
      type?: string;
      source?: { type?: string; media_type?: string; data?: string; url?: string };
      image_url?: { url?: string } | string;
      url?: string;
    };
    if (rec.type === "image") {
      const source = rec.source;
      if (source?.type === "base64" && source.data) {
        const media = source.media_type || "image/png";
        images.push({ src: `data:${media};base64,${source.data}` });
        continue;
      }
      if (source?.url) {
        images.push({ src: source.url });
        continue;
      }
    }
    if (rec.type === "image_url") {
      const url = typeof rec.image_url === "string" ? rec.image_url : rec.image_url?.url;
      if (url) images.push({ src: url });
    }
  }
  return images;
}

export function contentToThinking(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!block || typeof block !== "object") return "";
      const rec = block as { type?: string; thinking?: unknown };
      if (rec.type !== "thinking") return "";
      return typeof rec.thinking === "string" ? rec.thinking : "";
    })
    .filter(Boolean)
    .join("\n\n");
}

function parseToolInput(raw: unknown): Record<string, unknown> | undefined {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as Record<string, unknown>;
  }
  if (typeof raw !== "string" || !raw.trim()) return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

export function toolsFromContent(content: unknown): ReplayToolCall[] {
  if (!Array.isArray(content)) return [];
  const tools: ReplayToolCall[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const rec = block as { type?: string; name?: string; id?: unknown; input?: unknown };
    if (rec.type !== "tool_use") continue;
    tools.push({
      name: String(rec.name ?? "tool"),
      input: parseToolInput(rec.input),
      id: typeof rec.id === "string" && rec.id ? rec.id : undefined,
    });
  }
  return tools;
}

/** 一条工具结果 —— 从 tool_result 块或 tool/result 事件里读出来的。 */
export type ToolResultRecord = {
  callId: string;
  text: string;
  isError: boolean;
};

/**
 * 展平 tool_result 的 content：纯文本原样返回，富内容（图片等）逐块取 text，
 * 其余块按 JSON 兜底 —— 与 LLM 实际看到的内容一致。
 */
export function toolResultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return content === undefined || content === null ? "" : JSON.stringify(content, null, 2);
  const parts: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const rec = block as { type?: string; text?: unknown };
    if (typeof rec.text === "string") parts.push(rec.text);
    else parts.push(JSON.stringify(block, null, 2));
  }
  return parts.join("\n");
}

/** 从 user 消息的 content blocks 里取出全部 tool_result。 */
export function toolResultsFromContent(content: unknown): ToolResultRecord[] {
  if (!Array.isArray(content)) return [];
  const records: ToolResultRecord[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const rec = block as { type?: string; tool_use_id?: unknown; content?: unknown; is_error?: unknown };
    if (rec.type !== "tool_result") continue;
    const callId = typeof rec.tool_use_id === "string" ? rec.tool_use_id : "";
    if (!callId) continue;
    records.push({ callId, text: toolResultText(rec.content), isError: rec.is_error === true });
  }
  return records;
}

/** 从 ``tool/result`` 事件载荷里读出结果（data: {call_id, result, is_error}）。 */
export function toolResultFromEvent(data: unknown): ToolResultRecord | null {
  if (!data || typeof data !== "object") return null;
  const rec = data as { call_id?: unknown; result?: unknown; is_error?: unknown };
  const callId = typeof rec.call_id === "string" ? rec.call_id : "";
  if (!callId) return null;
  return { callId, text: toolResultText(rec.result), isError: rec.is_error === true };
}

/** 把一条工具结果挂到对应的 tool_use 上（按 id 匹配，返回是否命中）。 */
export function attachToolResult(tools: ReplayToolCall[], record: ToolResultRecord): boolean {
  const index = tools.findIndex((tool) => tool.id === record.callId);
  if (index < 0) return false;
  tools[index] = { ...tools[index], output: record.text, isError: record.isError };
  return true;
}

/** UI 附件（文件差异）：也按 tool_use id 挂回那次调用。 */
export function attachToolUi(
  tools: ReplayToolCall[],
  record: { callId: string; ui: FileDiffUi },
): boolean {
  const index = tools.findIndex((tool) => tool.id === record.callId);
  if (index < 0) return false;
  tools[index] = { ...tools[index], ui: record.ui };
  return true;
}

/** 校验一份 UI 附件，形状不对就当没有（别把脏数据喂给卡片）。 */
export function readFileDiffUi(raw: unknown): FileDiffUi | null {
  if (!raw || typeof raw !== "object") return null;
  const rec = raw as Partial<FileDiffUi>;
  if (rec.kind !== "file_diff" || typeof rec.path !== "string") return null;
  return {
    kind: "file_diff",
    path: rec.path,
    created: rec.created === true,
    added: typeof rec.added === "number" ? rec.added : 0,
    removed: typeof rec.removed === "number" ? rec.removed : 0,
    diff: typeof rec.diff === "string" ? rec.diff : null,
    truncated: rec.truncated === true,
  };
}

/** 从 `ui/tool-result` 事件载荷里读出附件。 */
export function toolUiFromEvent(data: unknown): { callId: string; ui: FileDiffUi } | null {
  if (!data || typeof data !== "object") return null;
  const rec = data as { call_id?: unknown; ui?: unknown };
  const callId = typeof rec.call_id === "string" ? rec.call_id : "";
  if (!callId) return null;
  const ui = readFileDiffUi(rec.ui);
  return ui ? { callId, ui } : null;
}

/** 从 `ui_tool_result` SDK 消息里读出附件（实况流用，字段名是 tool_use_id）。 */
export function toolUiFromMessage(payload: unknown): { callId: string; ui: FileDiffUi } | null {
  if (!payload || typeof payload !== "object") return null;
  const rec = payload as { tool_use_id?: unknown; ui?: unknown };
  const callId = typeof rec.tool_use_id === "string" ? rec.tool_use_id : "";
  if (!callId) return null;
  const ui = readFileDiffUi(rec.ui);
  return ui ? { callId, ui } : null;
}

function isToolResultContent(content: unknown): boolean {
  return (
    Array.isArray(content) &&
    content.some(
      (block) => block && typeof block === "object" && (block as { type?: string }).type === "tool_result",
    )
  );
}

/**
 * 按 call_id 把结果挂回发起它的 tool_use。工具结果总在下一条 assistant 消息之前到达，
 * 所以先翻本轮还没落定的 steps；万一落在已落定的消息里（轮在结果后立刻结束），
 * 再从最后一条消息倒着找一遍。
 */
function attachToPending(
  pending: readonly ReplayStep[],
  messages: readonly ReplayMessage[],
  record: ToolResultRecord,
): boolean {
  for (let i = pending.length - 1; i >= 0; i -= 1) {
    const tools = pending[i]?.tools;
    if (tools && attachToolResult(tools, record)) return true;
  }
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const steps = messages[i]?.steps;
    if (!steps) continue;
    for (let j = steps.length - 1; j >= 0; j -= 1) {
      const tools = steps[j]?.tools;
      if (tools && attachToolResult(tools, record)) return true;
    }
  }
  return false;
}

/** UI 附件走同样的查找路径：先本轮未落定的 steps，再倒着翻已落定的消息。 */
function attachUiToPending(
  pending: readonly ReplayStep[],
  messages: readonly ReplayMessage[],
  record: { callId: string; ui: FileDiffUi },
): boolean {
  for (let i = pending.length - 1; i >= 0; i -= 1) {
    const tools = pending[i]?.tools;
    if (tools && attachToolUi(tools, record)) return true;
  }
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const steps = messages[i]?.steps;
    if (!steps) continue;
    for (let j = steps.length - 1; j >= 0; j -= 1) {
      const tools = steps[j]?.tools;
      if (tools && attachToolUi(tools, record)) return true;
    }
  }
  return false;
}

function stepFromContent(id: string, content: unknown): ReplayStep | null {
  const text = contentToText(content).trim();
  const thinking = contentToThinking(content).trim();
  const tools = toolsFromContent(content);
  if (!text && !thinking && tools.length === 0) return null;
  return {
    id,
    text: text || undefined,
    thinking: thinking || undefined,
    tools: tools.length ? tools : undefined,
  };
}

export function foldAssistantTurn(parts: ReplayStep[]): {
  text: string;
  thinking?: string;
  steps?: ReplayStep[];
} {
  const last = parts[parts.length - 1];
  if (!last) return { text: "" };
  const prior = parts.slice(0, -1);
  const tail =
    last.thinking || last.tools?.length
      ? [{ id: `${last.id}-tail`, thinking: last.thinking, tools: last.tools }]
      : [];
  const steps = [...prior, ...tail];
  return {
    text: last.text ?? "",
    steps: steps.length ? steps : undefined,
  };
}

export type ReplayMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  images?: ReplayImage[];
  thinking?: string;
  steps?: ReplayStep[];
  durationMs?: number;
  seq?: number;
  /** 事件时间戳（毫秒）—— 气泡上的时刻。 */
  createdAt?: number;
  /** 本轮的 token 用量（只挂在 AI 消息上）。 */
  usage?: TurnTokens;
};

/** 从 ``usage`` 事件里读一份 usage 字典。字段缺失按 0 算。 */
function readUsageEvent(data: Record<string, unknown>): TurnTokens | null {
  const payload = data.usage;
  if (!payload || typeof payload !== "object") return null;
  const num = (key: string): number => {
    const value = (payload as Record<string, unknown>)[key];
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
  };
  return {
    inputTokens: num("input_tokens"),
    outputTokens: num("output_tokens"),
    cacheReadTokens: num("cache_read_input_tokens"),
    cacheCreationTokens: num("cache_creation_input_tokens"),
    model: typeof data.model === "string" && data.model ? data.model : undefined,
  };
}

export function messagesFromEvents(
  events: Array<{ seq: number; type: string; data: Record<string, unknown>; timestamp?: number }>,
): ReplayMessage[] {
  const messages: ReplayMessage[] = [];
  let pending: ReplayStep[] = [];
  let pendingThinking = "";
  let pendingSeq = 0;
  let pendingAt: number | undefined;
  let pendingUsage: TurnTokens | null = null;
  let turnStartedAt: number | null = null;

  const takeChunkThinking = (raw: Record<string, unknown>): string => {
    const chunk = raw.chunk;
    if (!chunk || typeof chunk !== "object") return "";
    return extractThinkingDelta({ event: chunk });
  };

  /**
   * 一轮里可能有多次 API 调用（工具往返），每次都会发一条 usage 事件 ——
   * 累加起来才是本轮的量，不能只取最后一条。
   */
  const addUsage = (incoming: TurnTokens) => {
    if (!pendingUsage) {
      pendingUsage = { ...incoming };
      return;
    }
    pendingUsage.inputTokens += incoming.inputTokens;
    pendingUsage.outputTokens += incoming.outputTokens;
    pendingUsage.cacheReadTokens += incoming.cacheReadTokens;
    pendingUsage.cacheCreationTokens += incoming.cacheCreationTokens;
    if (incoming.model) pendingUsage.model = incoming.model;
  };

  const flushAssistant = (durationMs?: number, boundarySeq?: number) => {
    // 这一刻和这份用量只属于本条消息：先摘出来，下面每条早退分支都得一起带走，
    // 否则上一轮的时间戳/用量会漏到下一条消息上。
    const at = pendingAt;
    const tokens = pendingUsage;
    pendingAt = undefined;
    pendingUsage = null;
    if (!pending.length && !pendingThinking.trim()) {
      pendingThinking = "";
      return;
    }
    if (!pending.length && pendingThinking.trim()) {
      pending.push({ id: `think-${messages.length}`, thinking: pendingThinking.trim() });
    } else if (pending.length && pendingThinking.trim()) {
      const last = pending[pending.length - 1];
      if (!last.thinking?.trim()) last.thinking = pendingThinking.trim();
    }
    pendingThinking = "";
    if (!pending.length) return;
    const last = pending[pending.length - 1];
    const seq = boundarySeq ?? pendingSeq;
    messages.push({
      id: last.id,
      role: "assistant",
      seq: seq > 0 ? seq : undefined,
      // 取最后一条内容的落定时间：它才是「这条回复写完」的时刻。
      createdAt: at,
      // 四个桶全为 0 说明这条 usage 事件没带数，挂在气泡上只是个「0 tok」。
      usage: tokens && usageTotal(tokens) > 0 ? tokens : undefined,
      ...foldAssistantTurn(pending),
      durationMs: durationMs && durationMs > 0 ? durationMs : undefined,
    });
    pending = [];
    pendingSeq = 0;
  };

  for (const event of events) {
    const content = event.data.content;
    if (event.type === "assistant/chunk") {
      pendingThinking += takeChunkThinking(event.data);
      if (typeof event.timestamp === "number") pendingAt = event.timestamp;
      continue;
    }
    if (event.type === "usage") {
      const tokens = readUsageEvent(event.data);
      if (tokens) addUsage(tokens);
      continue;
    }
    if (event.type === "tool/result") {
      const record = toolResultFromEvent(event.data);
      if (record) attachToPending(pending, messages, record);
      continue;
    }
    if (event.type === "ui/tool-result") {
      // 工具结果给界面的附件（文件差异）。事件是 ignorable 的：它不在模型上下文里，
      // 但落在会话日志里，所以重开历史会话照样能把差异卡片还原出来。
      const record = toolUiFromEvent(event.data);
      if (record) attachUiToPending(pending, messages, record);
      continue;
    }
    if (event.type === "user/message") {
      if (isToolResultContent(content)) {
        // 引擎合成的 tool_result 消息：不进用户气泡，只把结果挂回对应的工具调用。
        for (const record of toolResultsFromContent(content)) {
          attachToPending(pending, messages, record);
        }
        continue;
      }
      const text = contentToText(content);
      const images = contentToImages(content);
      if (!text.trim() && !images.length) continue;
      flushAssistant();
      messages.push({
        id: `evt-${event.seq}`,
        role: "user",
        text,
        images: images.length ? images : undefined,
        seq: event.seq,
        createdAt: typeof event.timestamp === "number" ? event.timestamp : undefined,
      });
    } else if (event.type === "assistant/message") {
      const step = stepFromContent(`evt-${event.seq}`, content);
      if (step) {
        if (!step.thinking?.trim() && pendingThinking.trim()) {
          step.thinking = pendingThinking.trim();
        }
        pendingThinking = "";
        pending.push(step);
        pendingSeq = event.seq;
        if (typeof event.timestamp === "number") pendingAt = event.timestamp;
      }
    } else if (event.type === "turn/start") {
      turnStartedAt = typeof event.timestamp === "number" ? event.timestamp : null;
    } else if (event.type === "turn/end") {
      const endedAt = typeof event.timestamp === "number" ? event.timestamp : null;
      const durationMs =
        turnStartedAt != null && endedAt != null ? Math.max(0, endedAt - turnStartedAt) : undefined;
      flushAssistant(durationMs, event.seq);
      turnStartedAt = null;
    }
  }
  flushAssistant();
  return messages;
}

/** 从后往前切出最近 ``turns`` 轮（以 user 消息为一轮起点）。 */
export function sliceLastUserTurns<T extends { role: string }>(
  messages: T[],
  turns = 3,
): { older: T[]; recent: T[] } {
  if (turns <= 0 || messages.length === 0) return { older: [], recent: messages };
  let seen = 0;
  let start = 0;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role !== "user") continue;
    seen += 1;
    if (seen >= turns) {
      start = i;
      break;
    }
  }
  if (seen < turns) return { older: [], recent: messages };
  return { older: messages.slice(0, start), recent: messages.slice(start) };
}
