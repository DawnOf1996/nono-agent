/** 写入用户消息正文的引用标记。模型能读到原文，界面再把它画成卡片。
 *
 * 标记按来源分四种：模型回复里选的是 `quoted-from-assistant-message`，终端里选的是
 * `quoted-from-terminal`，文件预览里选的是 `quoted-from-file`，内部浏览器里拾取的是
 * `quoted-from-browser`。模型只看到字面文本，
 * 但"这段是从哪抄来的"这件事值得写进去，否则一段报错栈和一段模型自己的话在它眼里没有区别。
 *
 * 引用可以带一句评论，用 `<user-comment>` 包在块内。四种来源都支持，没有评论时整个子标签
 * 不出现，退化成历史上的纯引用格式——旧会话里的消息照样解析得出来。
 *
 * 文件引用多两个属性：`path` 是相对工作区的路径（正斜杠，模型直接就能拿去 Read），
 * `lines` 是 `13` 或 `13-18`。行号只是给人看的提示，选区文本才是权威内容——文件被改过
 * 之后行号会漂，所以模型应该以原文为准。
 *
 * 浏览器引用来自内部浏览器的元素拾取：正文是「页面地址 + 选择器 + 元素 HTML」这类描述，
 * 模型读不到那个访客页，只能靠这段文字定位。
 *
 * 历史会话里只有前两种，解析要都认。
 */
export type QuoteSource = "assistant" | "terminal" | "file" | "browser";

const TAGS: Record<QuoteSource, { open: string; close: string }> = {
  assistant: { open: "<quoted-from-assistant-message>", close: "</quoted-from-assistant-message>" },
  terminal: { open: "<quoted-from-terminal>", close: "</quoted-from-terminal>" },
  file: { open: "<quoted-from-file>", close: "</quoted-from-file>" },
  browser: { open: "<quoted-from-browser>", close: "</quoted-from-browser>" },
};

/** 属性值里的引号得转义，否则 `path="a"b"` 会把标记结构撑坏。 */
const ATTR_ESCAPE = /"/g;
const ATTR_UNESCAPE = /&quot;/g;

function attribute(name: string, value: string): string {
  return `${name}="${value.replace(ATTR_ESCAPE, "&quot;")}"`;
}

/** 行号写成 `13` 或 `13-18`。两端都有且相同时只写一个。 */
export function formatLines(startLine?: number, endLine?: number): string | null {
  if (typeof startLine !== "number" || !Number.isFinite(startLine)) return null;
  const start = Math.max(1, Math.trunc(startLine));
  if (typeof endLine !== "number" || !Number.isFinite(endLine)) return String(start);
  const end = Math.max(start, Math.trunc(endLine));
  return end === start ? String(start) : `${start}-${end}`;
}

/** 解析 `13` 或 `13-18`，认不出来返回 null。 */
export function parseLines(value: string | null | undefined): { start: number; end: number } | null {
  if (typeof value !== "string") return null;
  const match = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(value.trim());
  if (!match) return null;
  const start = Number(match[1]);
  const end = match[2] === undefined ? start : Number(match[2]);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return { start: Math.min(start, end), end: Math.max(start, end) };
}

/**
 * 开标签 + 属性。捕获组 1 是标签名（`\1` 保证开闭成对），组 2 是原始属性串。
 * 属性限定成 `name="value"` 的成对形式：原文里裸奔的 `>` 不至于把开标签截断。
 */
const BLOCK_SOURCE =
  /<quoted-from-(assistant-message|terminal|file|browser)((?:\s+[a-z]+="[^"]*")*)\s*>\n?([\s\S]*?)\n?<\/quoted-from-\1>/g;

/** 块内的评论子标签。组 1 是评论正文。 */
const COMMENT_SOURCE = /<user-comment>\n?([\s\S]*?)\n?<\/user-comment>/g;

/** 引用原文里若混进标记本身，写出去前先把尖括号拆散，免得提前闭合别人的块。 */
const ANY_TAG = /<(\/?)(quoted-from-[a-z-]+|user-comment)>/g;
/** 拆散后的样子：空格插在标签名**后面**，和 shield 的模板一致，解蔽才能原样还原。 */
const SHIELDED_TAG = /<(\/?) (quoted-from-[a-z-]+|user-comment)>/g;

export const MAX_QUOTES = 8;
export const MAX_QUOTE_CHARS = 4000;
/** 用户设置里可调，硬边界在这。设置异常时夹回这个范围，不至于把上限改成 0 或 10000。 */
export const MAX_QUOTES_LIMIT = 64;
export const MAX_QUOTES_MIN = 1;

export function resolveMaxQuotes(configured?: number | null): number {
  if (typeof configured !== "number" || !Number.isFinite(configured)) return MAX_QUOTES;
  const value = Math.trunc(configured);
  if (value < MAX_QUOTES_MIN) return MAX_QUOTES_MIN;
  return Math.min(value, MAX_QUOTES_LIMIT);
}

export type QuoteDraft = {
  text: string;
  source: QuoteSource;
  path?: string;
  startLine?: number;
  endLine?: number;
  comment?: string;
};

export type QuotedBlock = {
  text: string;
  source: QuoteSource;
  path: string | null;
  startLine: number | null;
  endLine: number | null;
  comment: string | null;
};

export type QuotedMessage = {
  quotes: QuotedBlock[];
  body: string;
};

function sourceOf(name: string): QuoteSource {
  if (name === "terminal") return "terminal";
  if (name === "file") return "file";
  if (name === "browser") return "browser";
  return "assistant";
}

function shield(text: string): string {
  // 替换模板里必须显式写回斜杠（`$1`），否则闭合标签会被拆成开标签，
  // 解析时又被当成真的子标签边界，块里就多出一截碎片。
  return text.replace(ANY_TAG, "<$1 $2>");
}

function unshield(text: string): string {
  // 和 shield 对称：斜杠($1) + 标签名($2) 都得写回去。
  return text.replace(SHIELDED_TAG, "<$1$2>");
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
    .map((item) => {
      const tag = TAGS[item.source] ?? TAGS.assistant;
      const text = item.text.trim();
      const comment = item.comment?.trim();
      if (!text && !comment) return "";
      const attrs: string[] = [];
      // 路径写正斜杠：模型工具链认这个，换机器/换平台也还认。
      const path = item.path?.trim();
      if (path) attrs.push(attribute("path", path.replace(/\\/g, "/")));
      const lines = formatLines(item.startLine, item.endLine);
      if (lines) attrs.push(attribute("lines", lines));
      const open = attrs.length ? `${tag.open.slice(0, -1)} ${attrs.join(" ")}>` : tag.open;
      const inner = text ? shield(text) : "";
      const commentBlock = comment ? `<user-comment>\n${shield(comment)}\n</user-comment>` : "";
      const payload = [inner, commentBlock].filter(Boolean).join("\n");
      return payload ? `${open}\n${payload}\n${tag.close}` : "";
    })
    .filter(Boolean);
  const typed = body.trim();
  if (!blocks.length) return typed;
  if (!typed) return blocks.join("\n");
  return `${blocks.join("\n")}\n\n${typed}`;
}

function readAttribute(attrs: string, name: string): string | null {
  const match = new RegExp(`\\s${name}="([^"]*)"`).exec(attrs);
  if (!match) return null;
  const value = match[1].replace(ATTR_UNESCAPE, '"');
  return value.trim() || null;
}

/** 从块内摘出评论子标签，剩下的才是被引用的原文。
 *
 * 顺序要紧：先在**未解蔽**的内容里找真正的评论标签，剥掉之后再各自解蔽。
 * 反过来的话，用户在被引用的原文里手打的 `<user-comment>` 会先被还原成真标签，
 * 被误认成第二条评论的边界，块里就多出一截碎片。
 */
function splitComment(inner: string): { text: string; comment: string | null } {
  COMMENT_SOURCE.lastIndex = 0;
  const match = COMMENT_SOURCE.exec(inner);
  if (!match) return { text: unshield(inner).trim(), comment: null };
  const rest = `${inner.slice(0, match.index)}${inner.slice(match.index + match[0].length)}`;
  const comment = unshield(match[1] ?? "").trim();
  return { text: unshield(rest).trim(), comment: comment || null };
}

/** 从用户消息里拆出引用原文，剩下的才是用户自己写的内容。 */
export function splitQuotedMessage(text: string): QuotedMessage {
  const quotes: QuotedBlock[] = [];
  const rest = text.replace(BLOCK_SOURCE, (_all, name: string, attrs: string, inner: string) => {
    const { text: quoted, comment } = splitComment(String(inner));
    const source = sourceOf(String(name));
    if (!quoted && !comment) return "";
    const lines = parseLines(readAttribute(String(attrs ?? ""), "lines"));
    quotes.push({
      text: quoted,
      source,
      path: source === "file" ? readAttribute(String(attrs ?? ""), "path") : null,
      startLine: lines?.start ?? null,
      endLine: lines?.end ?? null,
      comment,
    });
    return "";
  });
  return {
    quotes,
    body: rest.replace(/\n{3,}/g, "\n\n").trim(),
  };
}