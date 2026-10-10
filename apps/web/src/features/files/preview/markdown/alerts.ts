/**
 * GitHub Alert：`> [!NOTE]` 这种块引用的识别与拆分。
 *
 * 为什么在 React 这一层做，而不是 remark / rehype：
 * 标记和正文在 remark 解析后是同一段里的一行软换行 —— `> [!NOTE]` 后面接正文时，
 * mdast 里就是一个文本节点 "[!NOTE]\n正文"，不是两段。到 hast 只能拿到一个带 \n 的
 * 字符串，想拆就得手工重切段落，很容易把 em/strong/link 这些行内节点切碎。
 * React 这层拿到的仍是 react-markdown 生成的元素树，只吃掉第一个文本节点的**前缀**，
 * 后面的行内节点原样保留，正文里的格式不会因为套了个提示块就掉。
 *
 * 还有个附带的好处：不用为它放宽 rehype-sanitize 的白名单。提示块上的 className
 * 是这里自己贴的，不放行任何来自用户 HTML 的 class —— 侧边预览读的是仓库里任意的
 * Markdown，白名单每松一格都是实打实的攻击面。
 */

import { Children, cloneElement, isValidElement, type ReactNode } from "react";

/** GitHub 支持的五种提示，顺序按 GitHub 自己排的。 */
export const ALERT_KINDS = ["note", "tip", "important", "warning", "caution"] as const;

export type AlertKind = (typeof ALERT_KINDS)[number];

/**
 * 标记必须在块引用第一段的开头 —— 段落中间提到的 [!NOTE] 不算，GitHub 也是这么判的。
 * 认错了比不认更糟：把一句人话染成彩色块是在骗读者。
 * 标记后面允许直接接正文（`[!NOTE] 正文`），GitHub 同样认这种写法。
 */
const MARKER = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*(?:\n|$|(?=\S))/i;

/** 标题跟着界面语言走，不照抄 GitHub 的英文。 */
const LABELS: Record<AlertKind, string> = {
  note: "注意",
  tip: "提示",
  important: "重要",
  warning: "警告",
  caution: "危险",
};

export type AlertSplit = {
  kind: AlertKind;
  label: string;
  /** 去掉标记之后的正文块；标记独占一段时，就是它后面那几段。 */
  children: ReactNode;
  /** 标记所在那一段的行号。提示块要换掉 blockquote，行号契约不能跟着断掉。 */
  line?: string;
};

type ParagraphProps = { children?: ReactNode; "data-line"?: string };

/** 第一段是不是以 Alert 标记开头。是的话把标记摘掉，连同类型一起返回。 */
export function splitAlert(children: ReactNode): AlertSplit | null {
  // react-markdown 在块与块之间会塞 "\n" 分隔文本节点（hast 里的原文换行），
  // 所以第一块不在 children[0] 上。空白文本节点一律跳过，只认真正的块元素 ——
  // 少了这一步，真实管线里第一项是 "\n"，标记就永远认不出来。
  const blocks = Children.toArray(children).filter((node) => typeof node !== "string" || node.trim() !== "");
  const first = blocks[0];
  // 标记只认块引用第一段的开头，GitHub 就是这么判的；段落中间提到的 [!NOTE] 不算。
  if (!isValidElement(first) || first.type !== "p") return null;

  const { children: inner0, "data-line": line } = first.props as ParagraphProps;
  const inner = Children.toArray(inner0);
  const head = inner[0];
  if (typeof head !== "string") return null;

  const match = MARKER.exec(head);
  if (!match) return null;

  const kind = match[1]!.toLowerCase() as AlertKind;
  const label = LABELS[kind];
  const rest = head.slice(match[0].length);
  const tail = inner.slice(1);

  // 标记自己占一整段：整段丢掉，正文就是它后面那些块。
  if (rest === "" && tail.length === 0) return { kind, label, line, children: blocks.slice(1) };

  // 标记后面还跟着正文（软换行连着的那一行）：把剩下的接回第一段。
  // 只改第一个文本节点的值，其余行内节点保持原样。
  return {
    kind,
    label,
    line,
    children: [cloneElement(first, {}, rest === "" ? tail : [rest, ...tail]), ...blocks.slice(1)],
  };
}