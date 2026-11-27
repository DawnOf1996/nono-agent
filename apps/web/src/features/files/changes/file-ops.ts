import type { DiffHunk, FileDiff, SessionEvent } from "@nono/protocol";

import type { ChatMessage } from "@/store/chat";

export type FileOpKind = "read" | "write" | "edit";

export type FileOp = {
  callId: string;
  kind: FileOpKind;
  path: string;
  time: number;
  running: boolean;
  isError: boolean;
  errorText?: string;
  /** 读到的正文、写入的全文，或编辑的新文本。 */
  after: string;
  /** 编辑前的片段；写入时为空。 */
  before: string;
};

const READ_TOOLS = new Set(["read_file", "read"]);
const WRITE_TOOLS = new Set(["write_file", "write"]);
const EDIT_TOOLS = new Set(["edit_file", "edit"]);

function kindOf(name: string): FileOpKind | undefined {
  if (READ_TOOLS.has(name)) return "read";
  if (WRITE_TOOLS.has(name)) return "write";
  if (EDIT_TOOLS.has(name)) return "edit";
  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function textField(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string") return value;
  }
  return "";
}

function pathOf(record: Record<string, unknown>): string {
  return textField(record, ["path", "file_path", "filePath"]);
}

/** 把会话事件里的 read_file / write_file / edit_file 收成按调用排列的操作。 */
export function fileOpsFromEvents(events: readonly SessionEvent[]): FileOp[] {
  const ops = new Map<string, FileOp>();
  const order: string[] = [];
  for (const event of events) {
    if (event.type === "tool/call") {
      const data = asRecord(event.data);
      const name = typeof data.name === "string" ? data.name : "";
      const kind = kindOf(name);
      const callId = typeof data.call_id === "string" ? data.call_id : "";
      if (!kind || !callId || ops.has(callId)) continue;
      const args = asRecord(data.args);
      const path = pathOf(args);
      if (!path) continue;
      ops.set(callId, {
        callId,
        kind,
        path,
        time: event.timestamp,
        running: true,
        isError: false,
        before: kind === "edit" ? textField(args, ["old_string", "oldString"]) : "",
        after:
          kind === "write"
            ? textField(args, ["content"])
            : kind === "edit"
              ? textField(args, ["new_string", "newString"])
              : "",
      });
      order.push(callId);
      continue;
    }
    if (event.type !== "tool/result") continue;
    const data = asRecord(event.data);
    const callId = typeof data.call_id === "string" ? data.call_id : "";
    const op = ops.get(callId);
    if (!op) continue;
    const isError = data.is_error === true;
    const result = typeof data.result === "string" ? data.result : "";
    op.running = false;
    op.isError = isError;
    op.time = event.timestamp || op.time;
    if (isError) op.errorText = result || "操作失败";
    else if (op.kind === "read") op.after = result;
  }
  return order.map((id) => ops.get(id)).filter((op): op is FileOp => op !== undefined);
}

/** 正在流式输出、事件还没落盘的文件操作。 */
export function fileOpsFromStreaming(messages: readonly ChatMessage[]): FileOp[] {
  const ops: FileOp[] = [];
  for (const message of messages) {
    if (!message.streaming || !message.steps) continue;
    message.steps.forEach((step, stepIndex) => {
      step.tools?.forEach((tool, toolIndex) => {
        const kind = kindOf(tool.name);
        if (!kind) return;
        const args = asRecord(tool.input);
        const path = pathOf(args);
        if (!path) return;
        ops.push({
          callId: `live:${message.id}:${stepIndex}:${toolIndex}`,
          kind,
          path,
          time: Date.now() / 1000,
          running: true,
          isError: false,
          before: kind === "edit" ? textField(args, ["old_string", "oldString"]) : "",
          after:
            kind === "write"
              ? textField(args, ["content"])
              : kind === "edit"
                ? textField(args, ["new_string", "newString"])
                : "",
        });
      });
    });
  }
  return ops;
}

export function groupOpsByFile(ops: readonly FileOp[]): Array<{ path: string; ops: FileOp[] }> {
  const groups = new Map<string, FileOp[]>();
  for (const op of ops) {
    const list = groups.get(op.path);
    if (list) list.push(op);
    else groups.set(op.path, [op]);
  }
  return [...groups.entries()]
    .map(([path, fileOps]) => ({
      path,
      ops: [...fileOps].sort((left, right) => right.time - left.time),
    }))
    .sort((left, right) => (right.ops[0]?.time ?? 0) - (left.ops[0]?.time ?? 0));
}

const DIFF_CELL_CAP = 160_000;

function lineOps(before: string[], after: string[]): Array<{ mark: " " | "+" | "-"; text: string }> {
  const cells = before.length * after.length;
  if (cells > DIFF_CELL_CAP) {
    return [
      ...before.map((text) => ({ mark: "-" as const, text })),
      ...after.map((text) => ({ mark: "+" as const, text })),
    ];
  }
  const next: number[][] = Array.from({ length: before.length + 1 }, () => new Array<number>(after.length + 1).fill(0));
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      next[i][j] = before[i] === after[j] ? next[i + 1][j + 1] + 1 : Math.max(next[i + 1][j], next[i][j + 1]);
    }
  }
  const rows: Array<{ mark: " " | "+" | "-"; text: string }> = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      rows.push({ mark: " ", text: before[i] });
      i += 1;
      j += 1;
    } else if (next[i + 1][j] >= next[i][j + 1]) {
      rows.push({ mark: "-", text: before[i] });
      i += 1;
    } else {
      rows.push({ mark: "+", text: after[j] });
      j += 1;
    }
  }
  while (i < before.length) {
    rows.push({ mark: "-", text: before[i] });
    i += 1;
  }
  while (j < after.length) {
    rows.push({ mark: "+", text: after[j] });
    j += 1;
  }
  return rows;
}

/** 把一次读 / 写 / 编辑收成 DiffView 能直接画的对比。读取显示正文，编辑对比替换片段。 */
export function opDiff(op: FileOp): FileDiff {
  const before = op.kind === "read" ? "" : op.before;
  const after = op.kind === "read" ? op.after : op.after;
  const read = op.kind === "read" && !op.isError;
  const oldLines = read ? after.split("\n") : before.split("\n");
  const newLines = read ? after.split("\n") : after.split("\n");
  const rows = read
    ? after.split("\n").map((text) => ({ mark: " " as const, text }))
    : lineOps(oldLines, newLines);
  const added = rows.filter((row) => row.mark === "+").length;
  const deleted = rows.filter((row) => row.mark === "-").length;
  const hunk: DiffHunk = {
    old_start: 1,
    old_lines: read ? newLines.length : oldLines.length,
    new_start: 1,
    new_lines: newLines.length,
    header: "",
    lines: rows.map((row) => `${row.mark}${row.text}`),
  };
  const status = op.kind === "write" ? "added" : op.kind === "read" ? "modified" : deleted && !added ? "deleted" : "modified";
  return {
    kind: "text",
    path: op.path,
    old_path: null,
    status,
    before: op.kind !== "write" && !read,
    after: true,
    hunks: hunk.lines.length ? [hunk] : [],
    added: read ? 0 : added,
    deleted: read ? 0 : deleted,
    coarse: !read && oldLines.length * newLines.length > DIFF_CELL_CAP,
  };
}
