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

export function extractTextDelta(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const record = payload as {
    event?: { type?: string; delta?: { type?: string; text?: string } };
  };
  const event = record.event;
  if (event?.type === "content_block_delta" && event.delta?.type === "text_delta") {
    return event.delta.text ?? "";
  }
  return "";
}

export function extractAssistantText(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const message = (payload as { message?: { content?: unknown } }).message;
  const content = message?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (block && typeof block === "object" && "text" in block) {
        const text = (block as { text?: unknown }).text;
        return typeof text === "string" ? text : "";
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

export function extractToolNames(payload: unknown): string[] {
  if (!payload || typeof payload !== "object") return [];
  const message = (payload as { message?: { content?: unknown } }).message;
  const content = message?.content;
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
