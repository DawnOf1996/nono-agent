/**
 * 正文里的 @文件 引用。
 *
 * 输入框的高亮层、对话区的高亮、以及从选择器里选中文件后往输入框插入的 token，
 * 都得对同一段文字得出同一个结论 —— 所以解析只有这一份，纯函数，不碰 DOM。
 *
 * 语法：@后面紧跟一段路径，路径里不能有空白；路径含空白时用双引号包起来
 * （@"我的 文件.md"）。@必须落在空白或行首，否则只是正文里的一个普通字符
 * （邮箱 user@example.com 就不会被当成引用）。
 */

/** 正文切出来的片段：普通文字，或者一段 @引用。 */
export type MentionSegment =
  | { kind: "text"; text: string }
  | {
      kind: "mention";
      text: string;
      path: string;
      isDir: boolean;
      /** 引用在正文里的位置：start 是 @ 的下标，end 是路径结束（不含）的下标。 */
      start: number;
      end: number;
    };

/** 引用在正文里的位置：start 是 @ 的下标，end 是路径结束（不含）的下标。 */
export type MentionSpan = {
  start: number;
  end: number;
  path: string;
};

function isSpace(char: string): boolean {
  return /\s/.test(char);
}

/**
 * 引号里的路径：扫到第一个没被转义的 " 为止，返回路径和结束位置。
 *
 * 没闭合就返回 null —— 那多半是用户正在敲，还没敲完，不能当引用处理。
 */
function readQuoted(text: string, from: number): { path: string; end: number } | null {
  let cursor = from;
  let path = "";
  while (cursor < text.length) {
    const char = text[cursor];
    if (char === "\\" && cursor + 1 < text.length) {
      path += text[cursor + 1];
      cursor += 2;
      continue;
    }
    if (char === '"') return { path, end: cursor + 1 };
    path += char;
    cursor += 1;
  }
  return null;
}

/**
 * 解析正文里的 @引用。
 *
 * 引号没闭合的按普通文字处理 —— 那多半是用户正在敲，还没敲完。
 * 光秃秃一个 @（后面还没跟路径）也是普通文字：它得留在正文里，
 * 否则输入框的高亮层会把它连同周围文字一起吞掉。
 */
export function parseMentions(text: string): MentionSegment[] {
  const segments: MentionSegment[] = [];
  // cursor 是还没归入任何片段的普通文字的起点。
  let cursor = 0;
  let index = 0;
  const pushText = (from: number, to: number) => {
    if (to > from) segments.push({ kind: "text", text: text.slice(from, to) });
  };
  while (index < text.length) {
    const at = text.indexOf("@", index);
    if (at < 0) break;
    // @ 必须落在空白或行首，否则是正文里的普通字符（邮箱之类）。
    if (at > 0 && !isSpace(text[at - 1])) {
      index = at + 1;
      continue;
    }
    let path = "";
    let end = at;
    if (text[at + 1] === '"') {
      const quoted = readQuoted(text, at + 2);
      if (!quoted) break;
      path = quoted.path;
      end = quoted.end;
    } else {
      let cursor2 = at + 1;
      while (cursor2 < text.length && !isSpace(text[cursor2]) && text[cursor2] !== '"') cursor2 += 1;
      path = text.slice(at + 1, cursor2);
      end = cursor2;
    }
    // 结尾的 / 表示这是个文件夹：目录没有预览页，展开它才对。
    const isDir = path.endsWith("/");
    if (isDir) path = path.slice(0, -1);
    if (!path) {
      // 只是个 @，还没跟路径：它是普通文字的一部分。
      pushText(cursor, at + 1);
      cursor = at + 1;
      index = at + 1;
      continue;
    }
    pushText(cursor, at);
    segments.push({ kind: "mention", text: text.slice(at, end), path, isDir, start: at, end });
    cursor = end;
    index = end;
  }
  pushText(cursor, text.length);
  return segments;
}

/** 只取引用里的路径（去掉 @ 和引号）。 */
export function mentionPaths(text: string): string[] {
  return parseMentions(text)
    .filter((item): item is Extract<MentionSegment, { kind: "mention" }> => item.kind === "mention")
    .map((item) => item.path);
}

/**
 * 一段 @引用 在正文里的结束位置（路径结束，不含）。
 *
 * 引号没闭合时返回整段文字的末尾 —— 那多半是用户还在敲。
 */
function mentionEnd(text: string, at: number): number {
  if (text[at + 1] === '"') {
    const quoted = readQuoted(text, at + 2);
    return quoted ? quoted.end : text.length;
  }
  let cursor = at + 1;
  while (cursor < text.length && !isSpace(text[cursor]) && text[cursor] !== '"') cursor += 1;
  return cursor;
}

/**
 * 找出光标前正在输入的那个 @引用。
 *
 * 返回 null 表示当前没有引用在输入（选择器应该收起）：没有 @、@ 落在词中间、
 * @ 后面已经跟了空白，或者引用已经定型（路径后面紧跟空白）、用引号闭合了。
 */
export function findMentionQuery(
  text: string,
  caret: number,
): { start: number; end: number; query: string } | null {
  const head = text.slice(0, Math.max(0, Math.min(caret, text.length)));
  // 从光标往回找：引用内容里不能有空白，撞见空白就说明这个引用已经结束。
  let at = -1;
  for (let index = head.length - 1; index >= 0; index -= 1) {
    const char = head[index];
    if (char === "@") {
      at = index;
      break;
    }
    if (isSpace(char)) break;
  }
  if (at < 0) return null;
  if (at > 0 && !isSpace(head[at - 1])) return null;
  // 已经定型的引用（路径后面紧跟空白）不算「正在输入」：
  // 光标落回引用中间时，选择器不该再弹出来 —— 它会挡住输入框，
  // 还会把退格变成逐字删，把 @文件名 拆成半截。
  const finished = mentionEnd(text, at);
  if (finished < text.length && isSpace(text[finished])) return null;
  const rest = head.slice(at + 1);
  if (rest.startsWith('"')) {
    // 引号里的路径：引号闭合了就说明这个引用已经敲完了。
    if (rest.includes('"', 1)) return null;
    return { start: at, end: head.length, query: rest.slice(1) };
  }
  return { start: at, end: head.length, query: rest };
}

/**
 * 光标**前面**那一整块 @引用 —— 「一次删掉整块」用的就是它。
 *
 * 命中条件：光标在引用中间、紧跟引用后面，或者紧跟引用后面那个空格
 * （选择器插进来的 token 末尾自带一个空格，光标正停在那儿）。
 * 光标正好停在引用**前面**不算 —— 那时候退格删的是前一个字，不是这块引用。
 */
export function mentionSpanBeforeCaret(text: string, caret: number): MentionSpan | null {
  const position = Math.max(0, Math.min(caret, text.length));
  let found: MentionSpan | null = null;
  for (const segment of parseMentions(text)) {
    if (segment.kind !== "mention") continue;
    if (segment.start < position && position <= segment.end + 1) {
      found = { start: segment.start, end: segment.end, path: segment.path };
    }
  }
  return found;
}

/**
 * 光标**后面**那一整块 @引用（Delete 键用）：光标在引用前面或中间时命中。
 */
export function mentionSpanAfterCaret(text: string, caret: number): MentionSpan | null {
  const position = Math.max(0, Math.min(caret, text.length));
  for (const segment of parseMentions(text)) {
    if (segment.kind !== "mention") continue;
    if (segment.start <= position && position < segment.end) {
      return { start: segment.start, end: segment.end, path: segment.path };
    }
  }
  return null;
}

/**
 * 整块删掉一段 @引用 时，真正要抹掉的范围。
 *
 * 引用后面那个空格也一起删 —— 选择器插进来的 token 末尾自带一个空格，
 * 只删引用会剩一个孤零零的空格在输入框里。
 */
export function mentionCutRange(
  text: string,
  span: MentionSpan,
): { start: number; end: number } {
  const end = text[span.end] === " " ? span.end + 1 : span.end;
  return { start: span.start, end };
}

/** 引用路径的末段（文件名）。路径用正斜杠，Windows 上也是。 */
export function mentionBaseName(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}

/** 引用路径所在的目录（正斜杠）。顶层文件返回空串。 */
export function mentionDirName(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  parts.pop();
  return parts.join("/");
}
