/**
 * 写入用户消息正文的引用标记。模型能读到原文，界面再把它画成卡片。
 *
 * 标记按来源分两种：模型回复里选的是 `quoted-from-assistant-message`，终端里选的是
 * `quoted-from-terminal`。模型只看到字面文本，但"这段是我从终端抄来的"这件事值得写进去，
 * 否则一段报错栈和一段模型自己的话在它眼里没有区别。历史会话里只有第一种，解析要都认。
 */
export type QuoteSource = "assistant" | "terminal";

const TAGS: Record<QuoteSource, { open: string; close: string }> = {
  assistant: { open: "<quoted-from-assistant-message>", close: "</quoted-from-assistant-message>" },
  terminal: { open: "<quoted-from-terminal>", close: "</quoted-from-terminal>" },
};

/** 两种标记一起匹配，捕获组 1 是来源、组 2 是原文；`\1` 保证开闭标签成对。 */
const BLOCK_SOURCE =
  /<quoted-from-(assistant-message|terminal)(?:\s[^>]*)?>\n?([\s\S]*?)\n?<\/quoted-from-\1>/g;

/** 引用原文里若混进标记本身，写出去前先把尖括号拆散，免得提前闭合别人的块。 */
const ANY_TAG = /<(\/?)(quoted-from-[a-z-]+)>/g;
const SHIELDED_TAG = /< (quoted-from-[a-z-]+)>/g;

export const MAX_QUOTES = 8;
export const MAX_QUOTE_CHARS = 4000;

export type QuoteDraft = {
  text: string;
  source: QuoteSource;
};

export type QuotedBlock = {
  text: string;
  source: QuoteSource;
};

export type QuotedMessage = {
  quotes: QuotedBlock[];
  body: string;
};

function sourceOf(name: string): QuoteSource {
  return name === "terminal" ? "terminal" : "assistant";
}

function shield(text: string): string {
  return text.replace(ANY_TAG, "<$1$2>");
}

function unshield(text: string): string {
  return text.replace(SHIELDED_TAG, "<$1>");
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
    .map((item) => ({ tag: TAGS[item.source] ?? TAGS.assistant, text: item.text.trim() }))
    .filter((item) => item.text)
    .map((item) => `${item.tag.open}\n${shield(item.text)}\n${item.tag.close}`);
  const typed = body.trim();
  if (!blocks.length) return typed;
  if (!typed) return blocks.join("\n");
  return `${blocks.join("\n")}\n\n${typed}`;
}

/** 从用户消息里拆出引用原文，剩下的才是用户自己写的内容。 */
export function splitQuotedMessage(text: string): QuotedMessage {
  const quotes: QuotedBlock[] = [];
  const rest = text.replace(BLOCK_SOURCE, (_all, name: string, inner: string) => {
    const value = unshield(String(inner)).trim();
    if (value) quotes.push({ text: value, source: sourceOf(String(name)) });
    return "";
  });
  return {
    quotes,
    body: rest.replace(/\n{3,}/g, "\n\n").trim(),
  };
}
