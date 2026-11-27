/** 把 README 里包着 Markdown 的 `<details>` / `<div>` 从正文里提出来。 */

import type { MarkdownSegment } from "./line-numbers";

export type { MarkdownSegment };

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const OPEN_TAG = /^ {0,3}<(details|div)\b([^>]*)>\s*$/i;
const SUMMARY = /<summary\b[^>]*>([\s\S]*?)<\/summary>/i;

function alignOf(attrs: string): "left" | "center" | "right" | null {
  const match = /\balign\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))/i.exec(attrs);
  const value = (match?.[1] ?? match?.[2] ?? match?.[3] ?? "").toLowerCase();
  if (value === "center" || value === "left" || value === "right") return value;
  return null;
}

function looksLikeInnerMarkdown(text: string): boolean {
  return /\n\s*\n/.test(text) || /^#{1,6}\s/m.test(text) || /^ {0,3}[-*+]\s/m.test(text);
}

function closePattern(tag: string): RegExp {
  return new RegExp(`^ {0,3}</${tag}>\\s*$`, "i");
}

function openPattern(tag: string): RegExp {
  return new RegExp(`^ {0,3}<${tag}\\b[^>]*>\\s*$`, "i");
}

/** 字符串里有几个换行。用来算剥掉前导空行之后行号该往前挪几格。 */
function countLines(text: string): number {
  let count = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) === 10) count += 1;
  }
  return count;
}

/** 围栏代码原样留下。只有独占一行的 details/div，且内部像 Markdown 时才拆开。 */
export function segmentReadmeHtml(source: string): MarkdownSegment[] {
  const lines = source.split("\n");
  const segments: MarkdownSegment[] = [];
  const buffer: string[] = [];
  // buffer 里第一行在源文件中的行号（1 起）。每收一行推进一格，flush 时写进段里。
  let bufferStart = 1;
  let fence: string | null = null;

  const flush = () => {
    if (!buffer.length) return;
    const text = buffer.join("\n");
    const offset = bufferStart;
    buffer.length = 0;
    if (text.trim()) segments.push({ kind: "markdown", text, lineOffset: offset });
  };

  for (let index = 0; index < lines.length; index += 1) {
    // 收一行进 buffer，顺带把它的起始行记上。各处 push 共用这一个入口，
    // 免得漏掉某一处导致后面所有段的行号整体偏移。
    const push = (value: string) => {
      if (!buffer.length) bufferStart = index + 1;
      buffer.push(value);
    };

    const line = lines[index] ?? "";
    if (fence) {
      push(line);
      if (line.trim().startsWith(fence[0]!) && line.trim().length >= fence.length && /^ {0,3}(`{3,}|~{3,})\s*$/.test(line)) {
        fence = null;
      }
      continue;
    }
    const opening = FENCE.exec(line);
    if (opening) {
      push(line);
      fence = opening[1] ?? "```";
      continue;
    }
    const tag = OPEN_TAG.exec(line);
    if (!tag) {
      push(line);
      continue;
    }
    const name = tag[1]!.toLowerCase();
    const close = closePattern(name);
    const nested = openPattern(name);
    const block = [line];
    let depth = 1;
    let cursor = index + 1;
    for (; cursor < lines.length; cursor += 1) {
      const current = lines[cursor] ?? "";
      block.push(current);
      if (nested.test(current)) depth += 1;
      else if (close.test(current)) {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    if (depth !== 0) {
      push(line);
      continue;
    }
    const inner = block.slice(1, -1).join("\n");
    if (!looksLikeInnerMarkdown(inner)) {
      for (const item of block) push(item);
      index = cursor;
      continue;
    }
    flush();
    // index 是开标签的 0 起下标；inner 是它下一行起的内容，
    // 第 1 行落在源文件第 index+2 行。必须在 index = cursor 之前取。
    const innerStart = index + 2;
    index = cursor;
    // 剥掉前导空行后，第 1 行又往后挪了几行，行号跟着挪。
    const offsetAfterTrim = (text: string): number => innerStart + countLines(text) - countLines(text.replace(/^\n+/, ""));
    if (name === "details") {
      const summary = SUMMARY.exec(inner);
      const head = summary ? inner.slice(0, summary.index) : "";
      const bodyRaw = summary ? inner.slice(summary.index + summary[0].length) : inner;
      const trimmed = bodyRaw.replace(/^\n+/, "");
      // summary 本身那一行已经被 SUMMARY 连着后面的换行一起吃掉了，
      // 所以只补 summary 之前的行数；剩下的靠剥前导空行补回来。
      const bodyOffset = innerStart + countLines(head) + (countLines(bodyRaw) - countLines(trimmed));
      segments.push({
        kind: "details",
        summary: summary?.[1]?.trim() || "详情",
        body: trimmed,
        open: /\bopen\b/i.test(tag[2] ?? ""),
        lineOffset: bodyOffset,
      });
    } else {
      segments.push({
        kind: "div",
        align: alignOf(tag[2] ?? ""),
        body: inner.replace(/^\n+/, ""),
        lineOffset: offsetAfterTrim(inner),
      });
    }
  }
  flush();
  return segments.length ? segments : [{ kind: "markdown", text: source, lineOffset: 1 }];
}
