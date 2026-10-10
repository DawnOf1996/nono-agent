/**
 * GitHub Alert 拆分的测试。认错了比不认更糟：把一句人话染成彩色块，
 * 或者把真提示块当普通引用放过去，都是在骗读者。
 *   node --experimental-strip-types --test src/features/files/preview/markdown/alerts.test.ts
 */
import assert from "node:assert/strict";
import { createElement, isValidElement, type ReactNode } from "react";
import { test } from "node:test";

import { splitAlert } from "./alerts.ts";

/**
 * react-markdown 交给 blockquote 组件的是它的 children，而且**块与块之间夹着 "\n"
 * 分隔文本节点**（hast 里的原文换行）。这个 helper 照原样复现，
 * 否则测的是一棵真实管线里不存在的树 —— 之前就是这么漏掉分隔节点的。
 */
function blockquote(...blocks: ReactNode[]): ReactNode {
  return [...blocks.flatMap((block) => ["\n", block]), "\n"];
}

/**
 * text 是段内第一个文本节点；line 是 remarkLineNumbers 注入的行号；rest 接在后面的行内节点。
 * 这三个一起给，才拼得出 react-markdown 交给 blockquote 组件的真实形状。
 */
function para(text: string, line?: string, ...rest: ReactNode[]): ReactNode {
  return createElement("p", line === undefined ? null : { "data-line": line }, text, ...rest);
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

test("[!NOTE] 独占一段时整段丢掉，正文就是后面那几段", () => {
  const alert = splitAlert(blockquote(para("[!NOTE]"), para("正文一"), para("正文二")));
  assert.equal(alert?.kind, "note");
  assert.equal(alert?.label, "注意");
  assert.equal(textOf(alert?.children ?? null), "正文一正文二");
});

test("标记后接软换行时，正文接回第一段", () => {
  // remark 解析后这是同一个段落里的一个 \n，不是两段 —— 拆的时候不能把正文切出去。
  const alert = splitAlert(blockquote(para("[!NOTE]\n正文")));
  assert.equal(alert?.kind, "note");
  assert.equal(textOf(alert?.children ?? null), "正文");
});

test("标记和正文同一行时也能拆", () => {
  const alert = splitAlert(blockquote(para("[!WARNING] 正文")));
  assert.equal(alert?.kind, "warning");
  assert.equal(textOf(alert?.children ?? null), "正文");
});

test("拆开后正文里的行内格式原样保留", () => {
  const strong = createElement("strong", null, "重点");
  const alert = splitAlert(blockquote(para("[!NOTE]\n正文 ", "7", strong)));
  const body = alert?.children;
  assert.ok(Array.isArray(body), "正文应该还是原来的块");
  const first = body[0];
  assert.ok(isValidElement(first), "第一块还是那个 <p>");
  const inner = (first.props as { children?: ReactNode }).children;
  assert.ok(Array.isArray(inner));
  assert.equal(inner[0], "正文 ");
  assert.ok(isValidElement(inner[1]), "<strong> 得留在原地");
  assert.equal(textOf(inner[1]), "重点");
});

test("块之间的换行分隔节点不影响识别", () => {
  // 真实管线里 children[0] 是 "\n" 而不是 <p>。这条锁的就是那个形状。
  const alert = splitAlert(blockquote(para("[!NOTE]"), para("正文")));
  assert.equal(alert?.kind, "note");
  assert.equal(textOf(alert?.children ?? null), "正文");
});

test("普通块引用原样放过", () => {
  assert.equal(splitAlert(blockquote(para("就一句引用"))), null);
});

test("标记不在开头不算提示块", () => {
  assert.equal(splitAlert(blockquote(para("见 [NOTE] 那节"))), null);
  // 少了感叹号：那只是正文里的一对方括号。
  assert.equal(splitAlert(blockquote(para("[NOTE] 没感叹号"))), null);
  assert.equal(splitAlert(blockquote(para("[!WAT] 未知类型"))), null);
});

test("第一块不是段落时不算提示块", () => {
  assert.equal(splitAlert(blockquote(createElement("h3", null, "[!NOTE]"))), null);
});

test("五种类型各归各的，行号跟着标记那一段", () => {
  const cases = [
    ["NOTE", "note"],
    ["TIP", "tip"],
    ["IMPORTANT", "important"],
    ["WARNING", "warning"],
    ["CAUTION", "caution"],
  ] as const;
  for (const [marker, kind] of cases) {
    const alert = splitAlert(blockquote(para(`[!${marker}]`, "7"), para("正文")));
    assert.equal(alert?.kind, kind, `[!${marker}] 应该是 ${kind}`);
    // 提示块换掉了 blockquote 外层，行号只能从标记这一段继承下来，
    // 否则选中正文时选区就换不出源文件行号了。
    assert.equal(alert?.line, "7");
    assert.equal(textOf(alert?.children ?? null), "正文");
  }
});

test("标记大小写混用也认", () => {
  assert.equal(splitAlert(blockquote(para("[!tip]")))?.kind, "tip");
});

test("只有标记没有正文也渲染提示块", () => {
  const alert = splitAlert(blockquote(para("[!TIP]")));
  assert.equal(alert?.kind, "tip");
  assert.equal(textOf(alert?.children ?? null), "");
});

test("没有行号标记时 line 为 undefined，不编一个出来", () => {
  const alert = splitAlert(blockquote(para("[!NOTE]")));
  assert.equal(alert?.line, undefined);
});