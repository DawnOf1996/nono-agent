/**
 * 流式 Markdown 的分块。
 *
 * 按顶层块切：围栏整块保留，空行分段，缩进/列表/引用的续段并回上一块。
 * 流式追加时只有最后一块在变，前面的块字符串保持不变，配合 memo 就不用重新解析整篇。
 *
 * 单独拆成 .ts 是因为这里能出真问题（$$ 块被空行劈开），而 MarkdownText 是 .tsx，
 * node --experimental-strip-types 跑不了 JSX —— 逻辑放这儿才测得到。
 */

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const LIST_ITEM = /^ {0,3}(?:[-*+]|\d{1,9}[.)])\s/;
const QUOTE = /^ {0,3}>/;
const INDENTED = /^(?: {4,}|\t)/;
/** 独占一行的 $$：块级公式的开关。同一行写完的 $$x$$ 不进这个状态。 */
const MATH_FENCE = /^ {0,3}\$\$[ \t]*$/;

function closesFence(line: string, fence: string): boolean {
  const match = FENCE_CLOSE.exec(line);
  return Boolean(match && match[1][0] === fence[0] && match[1].length >= fence.length);
}

function continuesPrevious(block: string, previous: string): boolean {
  if (INDENTED.test(block)) return true;
  if (LIST_ITEM.test(block) && LIST_ITEM.test(previous)) return true;
  if (QUOTE.test(block) && QUOTE.test(previous)) return true;
  return false;
}

export function splitMarkdownBlocks(text: string): string[] {
  const blocks: string[] = [];
  let buffer: string[] = [];
  let fence: string | null = null;
  let math = false;

  const flush = () => {
    if (!buffer.length) return;
    const block = buffer.join("\n");
    buffer = [];
    const previous = blocks[blocks.length - 1];
    if (previous !== undefined && continuesPrevious(block, previous)) {
      blocks[blocks.length - 1] = `${previous}\n\n${block}`;
      return;
    }
    blocks.push(block);
  };

  for (const line of text.split("\n")) {
    if (fence) {
      buffer.push(line);
      if (closesFence(line, fence)) {
        fence = null;
        flush();
      }
      continue;
    }
    // $$ 内部要先于围栏判断：LaTeX 里 verbatim 环境带 ``` 时，
    // 让围栏先匹配会把它当成代码块开头，公式块当场断成两半。
    if (math) {
      buffer.push(line);
      if (MATH_FENCE.test(line)) {
        math = false;
        flush();
      }
      continue;
    }
    if (MATH_FENCE.test(line)) {
      flush();
      buffer.push(line);
      math = true;
      continue;
    }
    const opening = FENCE_OPEN.exec(line);
    if (opening) {
      flush();
      buffer.push(line);
      fence = opening[1];
      continue;
    }
    if (!line.trim()) {
      if (buffer.length) flush();
      continue;
    }
    buffer.push(line);
  }
  flush();
  return blocks;
}

/**
 * 流式输出里尚未闭合的 $$ 块先按源码显示。
 *
 * 半截公式送进 KaTeX 只会渲染出一片红字（`\begin{aligned}` 缺 \end 之类），
 * 跟着每个新 token 闪一次，比晚半秒显示更难读。处理办法和未闭合的 mermaid
 * 围栏一样：这一块先不挂 rehypeKatex，等闭合了再渲染。
 *
 * 只管独占一行的 $$。行内 $a$ 没闭合时 micromark 会当成普通文字，
 * 等收尾的 $ 到了才升级成公式，本来就不会闪。
 */
export function isUnclosedMathBlock(block: string): boolean {
  let open = false;
  for (const line of block.split("\n")) {
    if (MATH_FENCE.test(line)) open = !open;
  }
  return open;
}