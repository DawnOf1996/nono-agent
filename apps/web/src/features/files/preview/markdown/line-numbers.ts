/**
 * Markdown 预览里「渲染位置 -> 源文件行号」的映射。
 *
 * 渲染出来的 DOM 不带行号，选区只能拿到渲染树里的节点。反推行号要过两关：
 *
 * 1. frontmatter 剥离（splitFrontmatter）把 YAML 头摘掉，正文第一行不再是源文件第 1 行。
 * 2. <details> / <div> 分段（segmentReadmeHtml）把正文拆成若干段，每段单独送进
 *    react-markdown，段内行号又从 1 重新数。
 *
 * 两关都得累加偏移，否则带 frontmatter 或带折叠块的文档，行号会整段错位——
 * 而错位的行号比没有行号更糟：模型会拿着它去改错的行。
 */

/**
 * 分段类型。lineOffset 是这一段的第 1 行对应源文件的第几行（1 起）——
 * frontmatter 剥离和 <details>/<div> 分段都会让渲染树里的行号重新从 1 数，
 * 换算回源文件必须靠它。
 */
export type MarkdownSegment = {
  kind: "markdown" | "details" | "div";
  /** markdown 段是整段文本；details/div 段是折叠内容。 */
  text?: string;
  summary?: string;
  body?: string;
  open?: boolean;
  align?: "left" | "center" | "right" | null;
  lineOffset: number;
};

/** 取段落文本：markdown 用 text，其余用 body。 */
export function segmentText(segment: MarkdownSegment): string {
  return segment.kind === "markdown" ? (segment.text ?? "") : (segment.body ?? "");
}

/** 段内第 n 行（1 起）对应的源文件行号。 */
export function sourceLineOf(segment: Pick<MarkdownSegment, "lineOffset">, lineInSegment: number): number {
  return segment.lineOffset + Math.max(1, lineInSegment) - 1;
}

/**
 * splitFrontmatter 之后，正文第 1 行对应源文件的第几行。
 *
 * 复刻 splitFrontmatter 的判定：开头 `---` 且后面还有一对 `---`，才算出偏移；
 * 否则整篇当正文，偏移为 0。BOM 也算一行的一部分，不额外加。
 *
 * 注意还要复刻它对 body 的 `replace(/^\n+/, "")`——收尾 `---` 和正文首行之间
 * 通常隔着空行，光按收尾位置 +1 算会差一格，正文第一段的行号就整体上移。
 */
export function frontmatterLineOffset(source: string): number {
  const text = source.startsWith("\ufeff") ? source.slice(1) : source;
  const lines = text.split("\n");
  const strip = (line: string) => (line.endsWith("\r") ? line.slice(0, -1) : line);
  if (strip(lines[0] ?? "") !== "---") return 0;
  for (let index = 1; index < lines.length; index += 1) {
    if (strip(lines[index] ?? "") !== "---") continue;
    // body = lines.slice(closing+1).join("\n").replace(/^\n+/, "")
    // 被吃掉的空行数，正好是正文首行相对收尾行往后挪的格数。
    let cursor = index + 1;
    while (cursor < lines.length && lines[cursor] === "") cursor += 1;
    return cursor + 1;
  }
  return 0;
}

/**
 * 节点上标着的 data-line 是段内相对行号，这里加上段偏移得到源文件行号。
 * 拿不到就返回 null —— 让调用方决定是回退到别处还是不显示行号，
 * 绝不用猜一个数填上去。
 *
 * DOM 那一侧统一由 chat/SelectionQuote.tsx 的 lineOf 处理（它还要兼顾
 * 代码预览的 data-line-number），这里只服务本文件的行号测试。
 */
export function resolveSourceLine(node: Element | null, segment: Pick<MarkdownSegment, "lineOffset">): number | null {
  const host = node?.closest<HTMLElement>("[data-line]");
  if (!host) return null;
  const raw = host.dataset.line;
  if (!raw) return null;
  const line = Number(raw);
  if (!Number.isFinite(line)) return null;
  return sourceLineOf(segment, line);
}