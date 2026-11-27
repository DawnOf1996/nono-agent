/**
 * 行级 diff —— 给工具卡片用。
 *
 * 只需要"两段文本之间的增删"，不需要 hunk 合并、相似度匹配或重命名检测：
 * 调用方拿到的就是一段被替换掉的原文和替换后的新文（edit_file 的 old_string /
 * new_string），规模也就在几十行量级。真正的文件级对比走服务端
 * `/app/changes/.../diff` 产出的 FileDiff，不要在这里造第二套。
 */

export type DiffLineKind = "add" | "del" | "same";

export type DiffLine = {
  kind: DiffLineKind;
  text: string;
  /** 该行在旧文件里的行号；新增行没有。 */
  oldNo?: number;
  /** 该行在新文件里的行号；删除行没有。 */
  newNo?: number;
};

/**
 * LCS 表的规模上限。
 *
 * 表是 (before+1)×(after+1) 个格子，超过这个量就退化成"全删 + 全增"：
 * 一张几百行的卡渲染出来没人看，但 DP 会把主线程按在地上几十毫秒。
 */
const MAX_CELLS = 1_000_000;

/** 按行切分。末尾空行不算一行 —— 文件 "a\n" 是一行，不是两行。 */
export function splitLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * 两段文本的行级差异。same 是两边都有的上下文行。
 *
 * start 给的是这两段文本在各自文件里的起始行号（默认都是 1）：edit_file 只把被替换
 * 的那一段传进来，工具结果里带着它落在文件里的真实行号（"first at line 42"），
 * 不带上这个偏移，卡片上的行号就从 1 开始编，看着像改的是文件开头。
 */
export function diffLines(
  before: readonly string[],
  after: readonly string[],
  start: { old?: number; new?: number } = {},
): DiffLine[] {
  const old0 = (start.old ?? 1) - 1;
  const new0 = (start.new ?? 1) - 1;
  if (before.length === 0) {
    return after.map((text, index) => ({ kind: "add", text, newNo: new0 + index + 1 }));
  }
  if (after.length === 0) {
    return before.map((text, index) => ({ kind: "del", text, oldNo: old0 + index + 1 }));
  }
  if (before.length * after.length > MAX_CELLS) {
    return [
      ...before.map((text, index) => ({ kind: "del" as const, text, oldNo: old0 + index + 1 })),
      ...after.map((text, index) => ({ kind: "add" as const, text, newNo: new0 + index + 1 })),
    ];
  }

  // lcs[i][j] = before[i..] 与 after[j..] 的最长公共子序列长度
  const lcs: number[][] = Array.from({ length: before.length + 1 }, () =>
    new Array<number>(after.length + 1).fill(0),
  );
  for (let i = before.length - 1; i >= 0; i -= 1) {
    const row = lcs[i];
    const next = lcs[i + 1];
    for (let j = after.length - 1; j >= 0; j -= 1) {
      row[j] = before[i] === after[j] ? next[j + 1] + 1 : Math.max(next[j], row[j + 1]);
    }
  }

  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      lines.push({ kind: "same", text: before[i], oldNo: old0 + i + 1, newNo: new0 + j + 1 });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      lines.push({ kind: "del", text: before[i], oldNo: old0 + i + 1 });
      i += 1;
    } else {
      lines.push({ kind: "add", text: after[j], newNo: new0 + j + 1 });
      j += 1;
    }
  }
  while (i < before.length) {
    lines.push({ kind: "del", text: before[i], oldNo: old0 + i + 1 });
    i += 1;
  }
  while (j < after.length) {
    lines.push({ kind: "add", text: after[j], newNo: new0 + j + 1 });
    j += 1;
  }
  return lines;
}

/** 差异里的增删行数，给卡片头部的 +N -M 用。 */
export function countChanges(lines: readonly DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.kind === "add") added += 1;
    else if (line.kind === "del") removed += 1;
  }
  return { added, removed };
}

/**
 * 解析 unified diff —— write_file 覆盖写时后端附的那段（nono_agent/tools/builtin/write.py）。
 *
 * 只认第一个 @@ 之后的内容：@@ 之前的 ---/+++ 是文件头，之后同形的行是被改动的正文
 * （删掉一行 `-- 注释` 在 diff 里就是 `--- 注释`），不能按文件头丢掉。遇到既不是
 * hunk 头、也不带 + - ␠ \ 前缀的行就收尾 —— 那已经不在差异里了。
 * 没有 hunk 就返回 null，由调用方决定回退成什么。
 */
export function parseUnifiedDiff(text: string): DiffLine[] | null {
  const lines: DiffLine[] = [];
  let inHunk = false;
  let oldNo = 0;
  let newNo = 0;
  for (const raw of text.split("\n")) {
    if (!inHunk) {
      // @@ -oldStart,oldCount +newStart,newCount @@ —— 行号从 hunk 头接着数。
      const header = /^@@+ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      if (header) {
        inHunk = true;
        oldNo = Number(header[1]);
        newNo = Number(header[2]);
      }
      continue;
    }
    if (raw.startsWith("@@")) {
      const header = /^@@+ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      if (header) {
        oldNo = Number(header[1]);
        newNo = Number(header[2]);
      }
      continue;
    }
    if (raw.startsWith("\\")) continue; // "\ No newline at end of file"
    if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: raw.slice(1), newNo });
      newNo += 1;
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "del", text: raw.slice(1), oldNo });
      oldNo += 1;
    } else if (raw === "" || raw.startsWith(" ")) {
      // 上下文行以空格开头；空行（""）是某些实现里"空上下文行"的简写，一并当上下文。
      lines.push({ kind: "same", text: raw.slice(1), oldNo, newNo });
      oldNo += 1;
      newNo += 1;
    } else break; // 差异到此为止，后面是别的内容
  }
  return lines.length ? lines : null;
}
