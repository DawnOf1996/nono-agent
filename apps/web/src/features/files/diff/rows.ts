import type { DiffHunk } from "@nono/protocol";

/** 渲染上限：超过这么多行的对比截断显示。 */
export const MAX_RENDERED_LINES = 5000;

/** 行内改动区间 [start, end)，按字符下标。 */
export type InlineRange = readonly [number, number];

export type DiffRow = {
  kind: "add" | "del" | "context";
  old?: number;
  new?: number;
  text: string;
  /** 与相邻的删除/新增行配对后，这一行真正变化的字符区间。 */
  changed?: InlineRange;
};

export type SplitCell = { no: number; text: string; kind: "add" | "del" | "context"; changed?: InlineRange };

export type SplitRow = { left?: SplitCell; right?: SplitCell };

/** 公共前缀、后缀之外的中段就是改动区间；两行完全不同时不标注（整行着色已足够）。 */
export function inlineRanges(oldText: string, newText: string): { old?: InlineRange; next?: InlineRange } {
  const min = Math.min(oldText.length, newText.length);
  let prefix = 0;
  while (prefix < min && oldText[prefix] === newText[prefix]) prefix += 1;
  let suffix = 0;
  while (suffix < min - prefix && oldText[oldText.length - 1 - suffix] === newText[newText.length - 1 - suffix]) {
    suffix += 1;
  }
  if (prefix === 0 && suffix === 0) return {};
  const oldRange: InlineRange = [prefix, oldText.length - suffix];
  const newRange: InlineRange = [prefix, newText.length - suffix];
  return {
    old: oldRange[1] > oldRange[0] ? oldRange : undefined,
    next: newRange[1] > newRange[0] ? newRange : undefined,
  };
}

/** 给 hunk 的每一行编号，并把相邻的删除段与新增段逐行配对标出行内改动。 */
export function hunkRows(hunk: DiffHunk): DiffRow[] {
  let oldNo = hunk.old_start;
  let newNo = hunk.new_start;
  const rows: DiffRow[] = hunk.lines.map((line) => {
    const text = line.slice(1);
    switch (line[0]) {
      case "+":
        return { kind: "add", new: newNo++, text };
      case "-":
        return { kind: "del", old: oldNo++, text };
      default:
        return { kind: "context", old: oldNo++, new: newNo++, text };
    }
  });
  let index = 0;
  while (index < rows.length) {
    if (rows[index].kind !== "del") {
      index += 1;
      continue;
    }
    const delStart = index;
    while (index < rows.length && rows[index].kind === "del") index += 1;
    const addStart = index;
    while (index < rows.length && rows[index].kind === "add") index += 1;
    const pairs = Math.min(addStart - delStart, index - addStart);
    for (let offset = 0; offset < pairs; offset += 1) {
      const del = rows[delStart + offset];
      const add = rows[addStart + offset];
      const ranges = inlineRanges(del.text, add.text);
      rows[delStart + offset] = { ...del, changed: ranges.old };
      rows[addStart + offset] = { ...add, changed: ranges.next };
    }
  }
  return rows;
}

/** 并排视图：每段删除与其后的新增逐行对齐，上下文两侧都有。 */
export function splitRows(rows: readonly DiffRow[]): SplitRow[] {
  const out: SplitRow[] = [];
  let dels: SplitCell[] = [];
  let adds: SplitCell[] = [];
  const flush = () => {
    for (let at = 0; at < Math.max(dels.length, adds.length); at += 1) {
      out.push({ left: dels[at], right: adds[at] });
    }
    dels = [];
    adds = [];
  };
  for (const row of rows) {
    if (row.kind === "del") dels.push({ no: row.old ?? 0, text: row.text, kind: "del", changed: row.changed });
    else if (row.kind === "add") adds.push({ no: row.new ?? 0, text: row.text, kind: "add", changed: row.changed });
    else {
      flush();
      out.push({
        left: { no: row.old ?? 0, text: row.text, kind: "context" },
        right: { no: row.new ?? 0, text: row.text, kind: "context" },
      });
    }
  }
  flush();
  return out;
}

export function hunkHeader(hunk: DiffHunk): string {
  const head = `@@ -${hunk.old_start},${hunk.old_lines} +${hunk.new_start},${hunk.new_lines} @@`;
  return hunk.header ? `${head} ${hunk.header}` : head;
}

/** 截到 MAX_RENDERED_LINES 行以内的 hunk 列表。 */
export function renderedHunks(hunks: readonly DiffHunk[]): { hunks: DiffHunk[]; truncated: boolean } {
  let budget = MAX_RENDERED_LINES;
  const kept: DiffHunk[] = [];
  let truncated = false;
  for (const hunk of hunks) {
    if (budget <= 0) {
      truncated = true;
      break;
    }
    if (hunk.lines.length > budget) {
      kept.push({ ...hunk, lines: hunk.lines.slice(0, budget) });
      truncated = true;
      budget = 0;
      continue;
    }
    kept.push(hunk);
    budget -= hunk.lines.length;
  }
  return { hunks: kept, truncated };
}

/** 两个 hunk 之间被省略的未改动行数（首个 hunk 之前的也算）。 */
export function gapBefore(hunks: readonly DiffHunk[], index: number): number {
  const hunk = hunks[index];
  if (index === 0) return Math.max(0, Math.min(hunk.old_start, hunk.new_start) - 1);
  const prev = hunks[index - 1];
  const prevEnd = prev.old_start + prev.old_lines;
  return Math.max(0, hunk.old_start - prevEnd);
}
