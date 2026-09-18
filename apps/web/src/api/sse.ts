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
    const rec = block as { type?: string; name?: string; input?: unknown };
    if (rec.type !== "tool_use") continue;
    tools.push({ name: String(rec.name ?? "tool"), input: parseToolInput(rec.input) });
  }
  return tools;
}

function isToolResultContent(content: unknown): boolean {
  return (
    Array.isArray(content) &&
    content.some(
      (block) => block && typeof block === "object" && (block as { type?: string }).type === "tool_result",
    )
  );
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

export function messagesFromEvents(
  events: Array<{ seq: number; type: string; data: Record<string, unknown>; timestamp?: number }>,
): Array<{
  id: string;
  role: "user" | "assistant";
  text: string;
  images?: ReplayImage[];
  thinking?: string;
  steps?: ReplayStep[];
  durationMs?: number;
  seq?: number;
}> {
  const messages: Array<{
    id: string;
    role: "user" | "assistant";
    text: string;
    images?: ReplayImage[];
    thinking?: string;
    steps?: ReplayStep[];
    durationMs?: number;
    seq?: number;
  }> = [];
  let pending: ReplayStep[] = [];
  let pendingThinking = "";
  let pendingSeq = 0;
  let turnStartedAt: number | null = null;

  const takeChunkThinking = (raw: Record<string, unknown>): string => {
    const chunk = raw.chunk;
    if (!chunk || typeof chunk !== "object") return "";
    return extractThinkingDelta({ event: chunk });
  };

  const flushAssistant = (durationMs?: number, boundarySeq?: number) => {
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
      continue;
    }
    if (event.type === "user/message") {
      if (isToolResultContent(content)) continue;
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
