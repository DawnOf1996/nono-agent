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

export function extractToolNames(payload: unknown): string[] {
  if (!payload || typeof payload !== "object") return [];
  const message = (payload as { message?: { content?: unknown } }).message;
  return toolNamesFromContent(message?.content);
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

export function toolNamesFromContent(content: unknown): string[] {
  if (!Array.isArray(content)) return [];
  return content
    .map((block) => {
      if (block && typeof block === "object" && (block as { type?: string }).type === "tool_use") {
        return String((block as { name?: string }).name ?? "tool");
      }
      return "";
    })
    .filter(Boolean);
}

function isToolResultContent(content: unknown): boolean {
  return (
    Array.isArray(content) &&
    content.some(
      (block) => block && typeof block === "object" && (block as { type?: string }).type === "tool_result",
    )
  );
}

export function messagesFromEvents(
  events: Array<{ seq: number; type: string; data: Record<string, unknown> }>,
): Array<{ id: string; role: "user" | "assistant"; text: string; tools?: string[]; thinking?: string }> {
  const messages: Array<{
    id: string;
    role: "user" | "assistant";
    text: string;
    tools?: string[];
    thinking?: string;
  }> = [];
  for (const event of events) {
    const content = event.data.content;
    if (event.type === "user/message") {
      if (isToolResultContent(content)) continue;
      const text = contentToText(content);
      if (!text.trim()) continue;
      messages.push({ id: `evt-${event.seq}`, role: "user", text });
    } else if (event.type === "assistant/message") {
      const text = contentToText(content);
      const thinking = contentToThinking(content);
      const tools = toolNamesFromContent(content);
      if (!text.trim() && tools.length === 0 && !thinking.trim()) continue;
      messages.push({
        id: `evt-${event.seq}`,
        role: "assistant",
        text,
        tools: tools.length ? tools : undefined,
        thinking: thinking || undefined,
      });
    }
  }
  return messages;
}
