const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const GLUED_MERMAID = /^(.*\S)[ \t]*((`{3,}|~{3,})[ \t]*mermaid\b.*)$/i;
const GLUED_HEADING_TABLE = /^(#{1,6}[ \t]+[^|\n]+?)\s*(\|.+\|)\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const MERMAID_OPEN = /^ {0,3}(`{3,}|~{3,})[ \t]*mermaid\b/i;

function closesFence(line: string, fence: string): boolean {
  const match = FENCE_CLOSE.exec(line);
  return Boolean(match && match[1][0] === fence[0] && match[1].length >= fence.length);
}

function hasCloser(lines: string[], start: number, fence: string): boolean {
  for (let index = start; index < lines.length; index += 1) {
    if (closesFence(lines[index] ?? "", fence)) return true;
  }
  return false;
}

/**
 * 模型常把标题和开栏写在同一行：`## 标题```mermaid`。
 * 围栏不在行首时，后面的图和正文都不会按代码块解析。
 * 只有后面确实有闭合栏时才拆开，避免把未写完的句子吞进图表。
 */
export function normalizeGluedMermaidFences(text: string): string {
  const lines = text.split(/\r?\n/);
  const out: string[] = [];
  let fence: string | null = null;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (fence) {
      out.push(line);
      if (closesFence(line, fence)) fence = null;
      continue;
    }
    const opening = FENCE_OPEN.exec(line);
    if (opening) {
      out.push(line);
      fence = opening[1];
      continue;
    }
    const glued = GLUED_MERMAID.exec(line);
    if (glued && hasCloser(lines, index + 1, glued[3] ?? "")) {
      out.push(glued[1] ?? "");
      out.push((glued[2] ?? "").trim());
      fence = glued[3] ?? null;
      continue;
    }
    out.push(line);
  }
  return out.join("\n");
}

/** 模型有时把标题和表头写在同一行：`### 说明| 列 | 列 |`。拆开后表格才能渲染。 */
export function normalizeGluedHeadingTables(text: string): string {
  const lines = text.split(/\r?\n/);
  const out: string[] = [];
  let fence: string | null = null;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (fence) {
      out.push(line);
      if (closesFence(line, fence)) fence = null;
      continue;
    }
    const opening = FENCE_OPEN.exec(line);
    if (opening) {
      out.push(line);
      fence = opening[1];
      continue;
    }
    const glued = GLUED_HEADING_TABLE.exec(line);
    if (glued && TABLE_RULE.test(lines[index + 1] ?? "")) {
      out.push((glued[1] ?? "").trimEnd());
      out.push((glued[2] ?? "").trim());
      continue;
    }
    out.push(line);
  }
  return out.join("\n");
}

function isUnclosedLanguageFence(block: string, opening: RegExp): boolean {
  const lines = block.split("\n");
  const match = opening.exec(lines[0] ?? "");
  if (!match) return false;
  const fence = match[1] ?? "";
  return !lines.slice(1).some((line) => closesFence(line, fence));
}

/** 流式输出里尚未闭合的 mermaid 围栏先按代码块显示，避免拿半截源码去渲染。 */
export function isUnclosedMermaidFence(block: string): boolean {
  return isUnclosedLanguageFence(block, MERMAID_OPEN);
}

/** 尚未闭合的 svg 围栏先按源码显示。 */
export function isUnclosedSvgFence(block: string): boolean {
  return isUnclosedLanguageFence(block, /^ {0,3}(`{3,}|~{3,})[ \t]*svg\b/i);
}
