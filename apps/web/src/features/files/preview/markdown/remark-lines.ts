/**
 * 给 Markdown 渲染出的块级节点标上它在源文件里的行号。
 *
 * 为什么需要：渲染后的 DOM 不带行号，用户在预览里选中一段文字时，
 * `getSelection()` 只能给出渲染树里的节点，反推不出"这是源文件第几行"。
 * 有了 data-line，选区端点往上找最近的带标记祖先就能拿到行号
 * （见 chat/SelectionQuote.tsx 的 lineOf）。
 *
 * 注意 rehype-sanitize 默认会剥掉未在 schema 里声明的属性，
 * 生成的 data-line-start 必须同步加进 MarkdownBody 的 schema，否则出了 mdast
 * 到不了 DOM。
 */

type Positioned = {
  position?: { start?: { line?: number } | null } | null;
  data?: Record<string, unknown>;
};

/**
 * 会带上 data-line 的节点。只有块级的才有"行"的语义。
 *
 * 刻意不含 html：rehype-raw 之后 HTML 片段不经过 mdast，注了也不会被
 * to-hast 搬到真实元素上，留着只会让人以为它生效了。
 * 也不含 inlineCode / text：行内片段不构成一行，标上去反而会干扰选区取行号。
 */
const ANNOTATED = new Set([
  "paragraph",
  "heading",
  "blockquote",
  "list",
  "listItem",
  "code",
  "table",
  "tableRow",
  "tableCell",
  "thematicBreak",
  // 公式两种都标上，理由和上面的 inline 排除条款不一样：
  //   math       是顶层块，外面没有 <p> 兜着，不标就真的取不到行号；
  //   inlineMath 一段话里可能跨好几行，公式所在行未必是 <p> 的起始行，
  //              跟着 <p> 报行号会差几行 —— 所以标它自己那行。
  // rehype-katex 会把这些节点整体替换掉，行号由 rehype-math-lines.ts 还回去。
  "math",
  "inlineMath",
]);

export function remarkLineNumbers() {
  return (tree: unknown): void => {
    const walk = (node: unknown): void => {
      if (!node || typeof node !== "object") return;
      const current = node as Positioned & { children?: unknown[]; type?: string };
      const line = current.position?.start?.line;
      if (typeof line === "number" && current.type && ANNOTATED.has(current.type)) {
        current.data = current.data ?? {};
        // hProperties 会被 mdast-util-to-hast 搬到真实元素的 props 上。
        // 键名用 hast 的属性名 dataLine：写 "data-line" 也能被它归一化过来，
        // 但 sanitize 的白名单是精确比对的，写驼峰才不会两边说法不一致。
        const properties = (current.data.hProperties ?? {}) as Record<string, unknown>;
        properties["dataLine"] = String(line);
        current.data.hProperties = properties;
      }
      const children = Array.isArray(current.children) ? current.children : null;
      if (children) for (const child of children) walk(child);
    };
    walk(tree);
  };
}