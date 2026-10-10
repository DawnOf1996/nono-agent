/**
 * 行号映射的测试。行号错了比没有行号更糟：模型会拿着它去改错的行。
 *   node --experimental-strip-types --test src/features/files/preview/markdown/line-numbers.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { frontmatterLineOffset, sourceLineOf, resolveSourceLine } from "./line-numbers.ts";
import { segmentReadmeHtml } from "./segments.ts";

/** 在模拟 DOM 上找最近的 data-line 祖先。 */
function fakeHost(line: string | undefined): Element | null {
  if (line === undefined) return null;
  return {
    dataset: { line },
    closest: () => ({ dataset: { line } }),
  } as unknown as Element;
}

test("frontmatter 没闭合时偏移为 0", () => {
  assert.equal(frontmatterLineOffset("# 标题\n正文"), 0);
});

test("普通文档没有 frontmatter，偏移为 0", () => {
  assert.equal(frontmatterLineOffset("# README\n\n正文"), 0);
});

test("有闭合 frontmatter 时偏移等于 YAML 结束行 + 1", () => {
  const source = "---\nname: x\n---\n# 标题\n正文";
  // 第 3 行是收尾的 ---，正文从第 4 行开始
  assert.equal(frontmatterLineOffset(source), 4);
});

test("frontmatter 行号偏移参与最终计算", () => {
  const segment = { lineOffset: frontmatterLineOffset("---\nname: x\n---\n# 标题") };
  // 段内第 1 行 -> 源文件第 4 行
  assert.equal(sourceLineOf(segment, 1), 4);
});

test("单段文档行号偏移为 1", () => {
  const segments = segmentReadmeHtml("# 标题\n\n正文\n\n## 第二");
  assert.equal(segments.length, 1);
  assert.equal(segments[0]?.lineOffset, 1);
  assert.equal(sourceLineOf(segments[0]!, 3), 3);
});

test("分段后每段行号不重置", () => {
  const source = ["# 标题", "", "<details>", "<summary>展开</summary>", "", "藏在里面的正文", "", "</details>", "", "## 后面的标题"].join("\n");
  const segments = segmentReadmeHtml(source);
  assert.ok(segments.length >= 3, `应该拆成多段，实际 ${segments.length}`);

  const details = segments.find((item) => item.kind === "details");
  assert.ok(details, "应该有 details 段");
  // 藏在里面的正文在源文件第 6 行；details 正文段首应当把它映回第 6 行附近
  assert.equal(details.body?.includes("藏在里面的正文"), true);

  const tail = segments[segments.length - 1];
  assert.ok(tail);
  // 最后一段首行是 details 闭标签后面那个空行（第 9 行），
  // 真正的 "## 后面的标题" 在段内第 2 行 -> 源文件第 10 行。
  assert.equal(tail.lineOffset, 9);
  assert.equal(sourceLineOf(tail, 2), 10);
});

test("details 正文里的行号能映回源文件", () => {
  const source = ["<details>", "<summary>展开</summary>", "", "关键内容在这里", "", "</details>"].join("\n");
  const details = segmentReadmeHtml(source).find((item) => item.kind === "details");
  assert.ok(details);
  // 段内第 1 行 "关键内容在这里" 在源文件第 4 行
  assert.equal(sourceLineOf(details, 1), 4);
});

test("段内行号加上段偏移得到源行号", () => {
  assert.equal(sourceLineOf({ lineOffset: 10 }, 1), 10);
  assert.equal(sourceLineOf({ lineOffset: 10 }, 5), 14);
});

test("拿不到 data-line 时返回 null，不猜", () => {
  assert.equal(resolveSourceLine(null, { lineOffset: 1 }), null);
  assert.equal(resolveSourceLine(fakeHost(undefined), { lineOffset: 1 }), null);
  assert.equal(resolveSourceLine(fakeHost("abc"), { lineOffset: 1 }), null);
});

test("resolveSourceLine 把段内相对行号换算成源行号", () => {
  assert.equal(resolveSourceLine(fakeHost("3"), { lineOffset: 40 }), 42);
});

test("围栏代码块内部不触发分段", () => {
  const source = ["```html", "<details>", "<summary>x</summary>", "```", "", "正文"].join("\n");
  const segments = segmentReadmeHtml(source);
  assert.equal(segments.length, 1);
  assert.equal(segments[0]?.kind, "markdown");
});