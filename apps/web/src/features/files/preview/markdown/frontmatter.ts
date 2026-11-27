/**
 * Markdown frontmatter（YAML 头）抽取。
 *
 * 为什么要单独抽：YAML 不是 Markdown。直接送进解析器，首行 `---` 会被当成
 * thematic break，结尾的 `---` 会被当成 setext 二级标题的下划线（整块变成一个假 h2），
 * `allowed-tools:` 下面的 `- Read` 是合法 Markdown 列表，会把后面所有字段吞成列表项，
 * 值里的 `#` `*` 还会被当行内标记解析。所以先摘出去，再渲染正文。
 *
 * 判定规则与后端 nono_agent/skills/loader.py 的 parse_skill_markdown 保持一致 ——
 * 预览里看到的 frontmatter 必须和运行时真正加载的是同一块，否则"看着有、读不出来"。
 * 关键的两条：
 * - 分隔符只吃掉行尾 \r\n，不吃别的空白。`--- ` 带尾空格不算分隔符。
 * - 没闭合就不算 frontmatter，整篇当正文（防止普通文档开头的 `---` 被吃掉）。
 */

export type Frontmatter = {
  /** 去掉一对 `---` 之后的原文；空字符串表示有头但里面没内容。 */
  raw: string;
  /** 去掉 frontmatter 之后的正文。 */
  body: string;
  /** 文档开头确实有一对 `---` 分隔符。 */
  present: boolean;
};

const BOM = "\ufeff";
const DELIMITER = "---";

/** 与后端 rstrip("\r\n") 对齐：只脱 \r，不 trim —— `--- ` 不是分隔符。 */
function stripCarriageReturn(line: string): string {
  return line.endsWith("\r") ? line.slice(0, -1) : line;
}

/**
 * 从 markdown 源码里切出 frontmatter。
 *
 * 只读，不改传入的字符串：保存回显走的是同一份 content.text，就地剥离会让用户
 * 点一次保存就把文件头吃掉。
 */
export function splitFrontmatter(source: string): Frontmatter {
  const text = source.startsWith(BOM) ? source.slice(BOM.length) : source;
  const lines = text.split("\n");
  if (stripCarriageReturn(lines[0] ?? "") !== DELIMITER) return { raw: "", body: text, present: false };

  let closing = -1;
  for (let index = 1; index < lines.length; index += 1) {
    if (stripCarriageReturn(lines[index] ?? "") === DELIMITER) {
      closing = index;
      break;
    }
  }
  if (closing === -1) return { raw: "", body: text, present: false };

  return {
    raw: lines.slice(1, closing).join("\n"),
    body: lines.slice(closing + 1).join("\n").replace(/^\n+/, ""),
    present: true,
  };
}

/**
 * 开头有 `---` 但还没闭合的半截 frontmatter。
 *
 * 只给流式场景用：模型正在逐字贴一份 SKILL.md，前几十帧里结束分隔符还没到，
 * 这时候按正文解析会先闪出一个假 h2，再跳成字段卡片。
 *
 * 要求块里至少有一行顶层 `key:` 才认 —— 只用 `---` 领头的文档（拿分隔线当装饰的
 * README）不该被当成 YAML。预览路径不走这里：文件是完整的，开头没闭合就是没闭合，
 * 后端 parse_skill_markdown 也不会剥它，预览得跟运行时看到同一份东西。
 */
export function splitOpenFrontmatterStart(source: string): { raw: string; body: string } | null {
  const text = source.startsWith(BOM) ? source.slice(BOM.length) : source;
  const lines = text.split("\n");
  if (stripCarriageReturn(lines[0] ?? "") !== DELIMITER) return null;

  const block: string[] = [];
  let index = 1;
  for (; index < lines.length; index += 1) {
    const line = stripCarriageReturn(lines[index] ?? "");
    if (line === DELIMITER) return null; // 已经闭合，交给 splitFrontmatter
    if (line.trim() === "") break;
    block.push(line);
  }
  if (!block.some((line) => KEY_LINE.test(line))) return null;
  return { raw: block.join("\n"), body: lines.slice(index).join("\n").replace(/^\n+/, "") };
}

/**
 * 一个顶层字段的展示形态，三选一。
 *
 * 刻意只解"顶层标量"和"顶层列表"两种最常见的情况：嵌套 mapping、块标量这些一律
 * 原样留着（raw），交给「看原文」兜底。宁可显示得笨一点，也不要把 YAML 解错成
 * 另一份意思 —— 前端不引 YAML 解析器正是这个取舍。
 */
export type FrontmatterField = {
  key: string;
  /** 顶层标量，去掉成对的首尾引号后的文本。 */
  scalar?: string;
  /** 顶层列表的每一项。 */
  list?: string[];
  /** 没硬解的原始片段（含 key 行），按代码块原样显示。 */
  raw?: string;
};

/** key 必须是顶格的、以字母数字/下划线开头 —— 缩进的 `key:` 是嵌套 mapping，不算顶层字段。 */
const KEY_LINE = /^([A-Za-z0-9_][A-Za-z0-9_.\-/]*)[ \t]*:[ \t]?(.*)$/;
const LIST_ITEM = /^[ \t]*-[ \t]?(.*)$/;
const FLOW_SEQ = /^\[(.*)\]$/;
/** 块标量指示符：| |- |+ > >- >+ */
const BLOCK_SCALAR = /^[|>][+-]?\d*$/;
/** 成对的首尾引号。 */
const QUOTED = /^(["'])([\s\S]*)\1$/;

function unquote(value: string): string {
  const match = QUOTED.exec(value);
  return match ? (match[2] ?? "") : value;
}

/** 空行后面若仍跟缩进内容，空行仍属于块标量（YAML 折叠标量的常见写法）。 */
function isBlockContinuation(lines: string[], index: number): boolean {
  const next = lines[index] ?? "";
  return next !== "" && /^[ \t]/.test(next);
}

/**
 * 把 frontmatter 原文拆成顶层字段，用于渲染元信息卡片。
 *
 * 逐行扫，不引依赖。解析不了的（嵌套 map、块标量、多行流式集合）整块退回原文。
 */
export function parseFrontmatterFields(raw: string): FrontmatterField[] {
  // 先把行尾 \r 去掉再逐行扫。JS 正则里 \r 和 \n 一样算行终止符，
  // `name: x\r` 匹配不上 KEY_LINE 结尾的 `$` —— CRLF 文件会一个字段都解析不出来，
  // 而 Windows 上写的 SKILL.md 十有八九就是 CRLF。
  const lines = raw.split("\n").map(stripCarriageReturn);
  const fields: FrontmatterField[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    const match = KEY_LINE.exec(line);
    // 顶层没有 key（注释、空行、异常 YAML）：没有归属就跳过，不硬塞给上一个字段。
    if (!match) {
      index += 1;
      continue;
    }
    const key = match[1] ?? "";
    const value = (match[2] ?? "").trim();
    index += 1;

    if (!value) {
      // 值在下面几行：吃掉连续的缩进块，全是列表项才算列表。
      const block: string[] = [];
      while (index < lines.length && isBlockContinuation(lines, index)) {
        block.push(lines[index] ?? "");
        index += 1;
      }
      if (!block.length) {
        fields.push({ key, scalar: "" });
      } else if (block.every((item) => LIST_ITEM.test(item))) {
        fields.push({ key, list: block.map((item) => unquote((LIST_ITEM.exec(item)?.[1] ?? "").trim())) });
      } else {
        // 嵌套 mapping：连 key 行一起当原文显示，读者能看到完整结构。
        fields.push({ key, raw: [line, ...block].join("\n") });
      }
      continue;
    }

    if (BLOCK_SCALAR.test(value)) {
      // 块标量：收到"下一行不再缩进"为止，中间允许夹空行。
      const block: string[] = [line];
      while (index < lines.length) {
        if (!isBlockContinuation(lines, index)) break;
        block.push(lines[index] ?? "");
        index += 1;
      }
      fields.push({ key, raw: block.join("\n") });
      continue;
    }

    const flow = FLOW_SEQ.exec(value);
    if (flow) {
      const inner = flow[1] ?? "";
      // 只认单层 [a, b]：再往里嵌（[[a]]、[a, {b: c}]）不拆，逗号会在字符串里骗人。
      if (!/[[\]{}]/.test(inner)) {
        fields.push({
          key,
          list: inner
            .split(",")
            .map((item) => unquote(item.trim()))
            .filter((item) => item !== ""),
        });
        continue;
      }
    }

    // 行尾注释（`name: x # 说明`）不做剥离：引号里的 # 也会被误伤，原样留着更诚实。
    fields.push({ key, scalar: unquote(value) });
  }

  return fields;
}

/** 卡片/摘要里的一行概括：优先 name，其次 description，都没有就用第一个有值的字段。 */
export function frontmatterSummary(fields: FrontmatterField[]): string {
  const pick = (key: string) =>
    fields.find((field) => field.key.toLowerCase() === key)?.scalar ?? "";
  const name = pick("name");
  const description = pick("description");
  if (name && description) return `${name} · ${description}`;
  return name || description || "";
}