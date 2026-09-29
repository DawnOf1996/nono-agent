/** 写入用户消息正文的引用标记。模型能读到原文，界面再把它画成卡片。 */
const OPEN = "<quoted-from-assistant-message>";
const CLOSE = "</quoted-from-assistant-message>";

const BLOCK_SOURCE =
  "<quoted-from-assistant-message(?:\\s[^>]*)?>\\n?([\\s\\S]*?)\\n?</quoted-from-assistant-message>";

export const MAX_QUOTES = 8;
export const MAX_QUOTE_CHARS = 4000;

export type QuoteDraft = {
  text: string;
};

export type QuotedMessage = {
  quotes: string[];
  body: string;
};

function shield(text: string): string {
  return text.replaceAll(OPEN, "< quoted-from-assistant-message>").replaceAll(CLOSE, "< /quoted-from-assistant-message>");
}

function unshield(text: string): string {
  return text.replaceAll("< quoted-from-assistant-message>", OPEN).replaceAll("< /quoted-from-assistant-message>", CLOSE);
}

export function clipQuote(text: string): { text: string; clipped: boolean } {
  const chars = Array.from(text);
  if (chars.length <= MAX_QUOTE_CHARS) return { text, clipped: false };
  return { text: `${chars.slice(0, MAX_QUOTE_CHARS).join("")}…`, clipped: true };
}

export function quotePreview(text: string, max = 16): string {
  const flat = text.replace(/\s+/g, " ").trim();
  const chars = Array.from(flat);
  if (chars.length <= max) return flat;
  return `${chars.slice(0, max).join("")}…`;
}

/** 把输入框里的引用卡片和正文合成一条可持久化的用户消息。 */
export function composeQuotedMessage(quotes: readonly QuoteDraft[], body: string): string {
  const blocks = quotes
    .map((item) => item.text.trim())
    .filter(Boolean)
    .map((text) => `${OPEN}\n${shield(text)}\n${CLOSE}`);
  const typed = body.trim();
  if (!blocks.length) return typed;
  if (!typed) return blocks.join("\n");
  return `${blocks.join("\n")}\n\n${typed}`;
}

/** 从用户消息里拆出引用原文，剩下的才是用户自己写的内容。 */
export function splitQuotedMessage(text: string): QuotedMessage {
  const quotes: string[] = [];
  const rest = text.replace(new RegExp(BLOCK_SOURCE, "g"), (_all, inner: string) => {
    const value = unshield(String(inner)).trim();
    if (value) quotes.push(value);
    return "";
  });
  return {
    quotes,
    body: rest.replace(/\n{3,}/g, "\n\n").trim(),
  };
}
