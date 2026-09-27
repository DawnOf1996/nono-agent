/** 把 README 里包着 Markdown 的 `<details>` / `<div>` 从正文里提出来。 */

export type MarkdownSegment =
  | { kind: "markdown"; text: string }
  | { kind: "details"; summary: string; body: string; open: boolean }
  | { kind: "div"; align: "left" | "center" | "right" | null; body: string };

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

/** 围栏代码原样留下。只有独占一行的 details/div，且内部像 Markdown 时才拆开。 */
export function segmentReadmeHtml(source: string): MarkdownSegment[] {
  const lines = source.split("\n");
  const segments: MarkdownSegment[] = [];
  const buffer: string[] = [];
  let fence: string | null = null;

  const flush = () => {
    if (!buffer.length) return;
    const text = buffer.join("\n");
    buffer.length = 0;
    if (text.trim()) segments.push({ kind: "markdown", text });
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (fence) {
      buffer.push(line);
      if (line.trim().startsWith(fence[0]!) && line.trim().length >= fence.length && /^ {0,3}(`{3,}|~{3,})\s*$/.test(line)) {
        fence = null;
      }
      continue;
    }
    const opening = FENCE.exec(line);
    if (opening) {
      buffer.push(line);
      fence = opening[1] ?? "```";
      continue;
    }
    const tag = OPEN_TAG.exec(line);
    if (!tag) {
      buffer.push(line);
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
      buffer.push(line);
      continue;
    }
    const inner = block.slice(1, -1).join("\n");
    if (!looksLikeInnerMarkdown(inner)) {
      buffer.push(...block);
      index = cursor;
      continue;
    }
    flush();
    index = cursor;
    if (name === "details") {
      const summary = SUMMARY.exec(inner);
      const body = summary ? inner.slice(summary.index + summary[0].length) : inner;
      segments.push({
        kind: "details",
        summary: summary?.[1]?.trim() || "详情",
        body: body.replace(/^\n+/, ""),
        open: /\bopen\b/i.test(tag[2] ?? ""),
      });
    } else {
      segments.push({ kind: "div", align: alignOf(tag[2] ?? ""), body: inner.replace(/^\n+/, "") });
    }
  }
  flush();
  return segments.length ? segments : [{ kind: "markdown", text: source }];
}
