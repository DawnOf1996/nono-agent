/**
 * 渲染公式，并把行号还回去。
 *
 * rehype-katex 是把 <code class="language-math math-display"> 整个换成 katex 的
 * span，remarkLineNumbers 挂在上面的 data-line 跟着一起没了 —— 选中公式里的字时
 * SelectionQuote.lineOf 一路往上找不到 [data-line]，引用就只有内容没有行号。
 *
 * 补救：渲染前按文档顺序记下每个公式元素的行号，渲染后按同样的顺序发回 katex 根节点。
 * 一一对应是成立的 —— rehype-katex 对每个命中的元素做一次 splice 整体替换，
 * 遍历顺序就是文档顺序，替换结果也留在原位。
 *
 * 聊天侧不挂这个：助手回复本来就没有行号概念（hostOf 给的是 assistant）。
 */

import rehypeKatex, { type Options } from "rehype-katex";

/** 和 remark-lines.ts 一致：本仓库不引 hast 的类型，只写用得到的结构。 */
type HastNode = {
  type?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

/** rehype-katex 认的三个 class，见它的 lib/index.js。 */
const MATH_CLASSES = new Set(["language-math", "math-inline", "math-display"]);

function classNamesOf(node: HastNode): string[] {
  const className = node.properties?.className;
  return Array.isArray(className) ? className.filter((item): item is string => typeof item === "string") : [];
}

function isMathElement(node: HastNode): boolean {
  return node.type === "element" && classNamesOf(node).some((name) => MATH_CLASSES.has(name));
}

function isKatexRoot(node: HastNode): boolean {
  return node.type === "element" && node.tagName === "span" && classNamesOf(node).includes("katex");
}

function readLine(node: HastNode | null): string | undefined {
  // 键名是 hast 的属性名 dataLine，不是 HTML 里的 data-line —— 见 schema.ts 的说明。
  const line = node?.properties?.dataLine;
  return typeof line === "string" || typeof line === "number" ? String(line) : undefined;
}

/**
 * 渲染前：按文档顺序收集公式元素的行号。
 *
 * 块级公式的行号**不在公式元素自己身上**：remark-math 把 math 节点设成
 * hName:"pre" + hChildren，所以 data-line 挂在外层那个 <pre> 上，
 * 而 rehype-katex 命中的、带 language-math 的 <code> 是 hChildren 里的产物。
 * 只认自己身上的话块级公式一个都收集不到 —— 行内公式才在自己身上。
 */
function collectLines(node: HastNode, parent: HastNode | null, out: string[]): void {
  if (isMathElement(node)) {
    const line = readLine(node) ?? readLine(parent);
    if (line !== undefined) out.push(line);
    // 命中就停：rehype-katex 不会往公式内部再找目标，内层不会有别的公式元素。
    return;
  }
  for (const child of node.children ?? []) collectLines(child, node, out);
}

/** 渲染后：按同样的顺序把行号贴回 katex 根节点。 */
function restoreLines(node: HastNode, lines: string[], cursor: { index: number }): void {
  if (isKatexRoot(node)) {
    const line = lines[cursor.index];
    cursor.index += 1;
    if (line !== undefined) node.properties = { ...node.properties, dataLine: line };
    return;
  }
  for (const child of node.children ?? []) restoreLines(child, lines, cursor);
}

export function rehypeMathLines(options?: Options) {
  const render = rehypeKatex(options);
  return function transformer(tree: HastNode, file: unknown): void {
    const lines: string[] = [];
    collectLines(tree, null, lines);
    (render as unknown as (tree: unknown, file: unknown) => undefined)(tree, file);
    restoreLines(tree, lines, { index: 0 });
  };
}