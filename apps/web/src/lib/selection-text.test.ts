/**
 * 选区引用的测试。
 *
 * 背景：KaTeX 把一个公式渲染成两棵树 —— .katex-mathml（clip 成 1px 的 MathML，
 * 里面藏着 <annotation> 的 TeX 原文）和 .katex-html（人看的字形）。两棵都在 DOM 里，
 * 直接 selection.toString() 会把两棵文字全抄一遍。selectionText 负责还原成 LaTeX。
 *
 * 用 jsdom 建 DOM，**内容由真实的 katex.renderToString 生成**：手搓的假结构
 * 会随 KaTeX 升级漂移，到时候测试全绿而实际是坏的。
 *
 *   node --experimental-strip-types --test src/lib/selection-text.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { JSDOM } from "jsdom";
import katex from "katex";

import { isDisplayMath, katexSourceOf, selectionText, wrapMath } from "./selection-text.ts";

function katexHtml(tex: string, displayMode = false): string {
  return katex.renderToString(tex, { displayMode, throwOnError: false });
}

function setup(innerHtml: string): { doc: Document; root: Element } {
  const dom = new JSDOM(`<div id="root">${innerHtml}</div>`);
  const doc = dom.window.document as unknown as Document;
  return { doc, root: doc.getElementById("root") as unknown as Element };
}

/** 选中最外层节点的整个内容区。 */
function selectContents(doc: Document, node: Element): Range {
  const range = doc.createRange();
  range.selectNodeContents(node);
  return range;
}

/** 选中最外层节点的整个内容区。 */
function selectAll(doc: Document, root: Element): Range {
  return selectContents(doc, root);
}

function pick(root: Element, selector: string): Element {
  const hit = root.querySelector(selector);
  assert.ok(hit, `夹具里应该找得到 ${selector}`);
  return hit as unknown as Element;
}

test("纯文字选区原样返回，不被公式逻辑改写", () => {
  const { doc, root } = setup("<p>就是一段普通文字。</p>");
  assert.equal(selectionText(selectAll(doc, root)), "就是一段普通文字。");
});

test("选整个行内公式，还原成 $...$", () => {
  const { doc, root } = setup(`<p>质能方程是${katexHtml("E = mc^2")}。</p>`);
  const range = selectContents(doc, pick(root, ".katex"));
  assert.equal(selectionText(range), "$E = mc^2$");
});

test("选整个块级公式，还原成 $$...$$", () => {
  const { doc, root } = setup(katexHtml("\\frac{a}{b}", true));
  const range = selectContents(doc, pick(root, ".katex"));
  assert.equal(selectionText(range), "$$\n\\frac{a}{b}\n$$");
});

test("跨着公式拖选，正文保留、公式夹在中间还原", () => {
  const { doc, root } = setup(`<p>前面${katexHtml("a+b")}后面</p>`);
  assert.equal(selectionText(selectAll(doc, root)), "前面$a+b$后面");
});

test("一整段里有两个公式，两个都还原且顺序不乱", () => {
  const { doc, root } = setup(`<p>甲${katexHtml("x^2")}乙${katexHtml("y^2")}丙</p>`);
  assert.equal(selectionText(selectAll(doc, root)), "甲$x^2$乙$y^2$丙");
});

test("只框住公式的一部分，也按整个公式还原，不抄字形乱码", () => {
  // 选区完全落在 .katex-html 里时，clone 出来的碎片里连一个完整的 .katex 都没有，
  // 走通用分支会退化成字形文字。这条盯的是走"所在的公式"那条分支。
  const { doc, root } = setup(`<p>质能方程是${katexHtml("E = mc^2")}。</p>`);
  const range = selectContents(doc, pick(root, ".katex-html"));
  assert.equal(selectionText(range), "$E = mc^2$");
});

test("选区从正文中间起、到公式中间止", () => {
  const { doc, root } = setup(`<p>前面${katexHtml("a+b")}后面</p>`);
  const p = pick(root, "p");
  const katexEl = pick(root, ".katex");
  const range = doc.createRange();
  range.setStart(p.firstChild!, 0);
  range.setEnd(katexEl, 1); // 只吃到 .katex 自身的一部分
  const text = selectionText(range);
  assert.ok(text.includes("$a+b$"), `公式要还原成源码，实际：${JSON.stringify(text)}`);
  assert.equal(text.includes("<"), false, "不能漏出标签");
});

test("结果里绝不该出现 MathML 副本的字形文字", () => {
  const { doc, root } = setup(`<p>质能方程是${katexHtml("E = mc^2")}。</p>`);
  const range = selectContents(doc, pick(root, ".katex"));
  const text = selectionText(range);

  // 把天真的做法并排跑一遍，钉住这次修的到底是什么。
  // selection.toString() 在这里给的是 "E=mc2E = mc^2E=mc2" 这种三份叠加：
  // MathML 的 <mi>/<mo> 字符、annotation 里的源码、可见字形各抄一遍。
  const naive = range.toString();
  assert.notEqual(naive, text, "天真做法和本函数的输出必须不同，否则这条测试没在测东西");
  assert.equal(/E\s*=\s*m\s*c\s*2.*E\s*=/s.test(text), false, `不该有重复的字形文字：${text}`);
});

test("块级和行内判定：closest 必须在原树上问", () => {
  const { root } = setup(`${katexHtml("x", true)}<p>${katexHtml("y", false)}</p>`);
  const all = [...root.querySelectorAll(".katex")];
  assert.equal(all.length, 2);
  assert.equal(isDisplayMath(all[0] as unknown as Element), true, "第一个是块级");
  assert.equal(isDisplayMath(all[1] as unknown as Element), false, "第二个是行内");
});

test("多行公式的换行结构在引用里保留", () => {
  const { doc, root } = setup(katexHtml("\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}", true));
  const range = selectContents(doc, pick(root, ".katex"));
  const text = selectionText(range);
  assert.ok(text.startsWith("$$\n"), "以 $$ 开头");
  assert.ok(text.includes("\n"), "内部换行不能被压掉");
  assert.ok(text.endsWith("\n$$"), "以换行 + $$ 结尾");
});

test("公式被 sanitize 之类的逻辑动过也照样还原", () => {
  // 顺带确认 katexSourceOf 不依赖 katex 的顶层结构，只认 annotation。
  const { doc, root } = setup(`<div class="x">${katexHtml("a+b")}</div>`);
  const range = selectContents(doc, pick(root, ".katex"));
  assert.equal(selectionText(range), "$a+b$");
});

// ---- 纯函数 ----

/** 只需要 querySelector 就够了 —— katexSourceOf 对元素的全部要求就这么点。 */
function fakeKatex(tex: string | null) {
  return {
    querySelector(selectors: string) {
      if (!selectors.includes("annotation") || tex === null) return null;
      return { textContent: tex };
    },
  };
}

test("从 annotation 里取出 TeX 原文并去掉首尾空白", () => {
  assert.equal(katexSourceOf(fakeKatex("\n  E = mc^2  \n")), "E = mc^2");
});

test("取不到 annotation 时返回 null", () => {
  // 选到半个公式时副本里的 annotation 也是断的，这时宁可整段不抄。
  assert.equal(katexSourceOf(fakeKatex(null)), null);
});

test("空白源码不算源码", () => {
  assert.equal(katexSourceOf(fakeKatex("   \n  ")), null);
});

test("行内公式补单 $ 包裹", () => {
  assert.equal(wrapMath("a+b", false), "$a+b$");
});

test("块级公式补 $$ 包裹并换行", () => {
  assert.equal(wrapMath("\\frac{a}{b}", true), "$$\n\\frac{a}{b}\n$$");
});

test("块级公式的多行结构原样保留", () => {
  // aligned 这类环境靠换行分隔，包裹时不能把内部换行压掉 —— 压了模型就还原不回去。
  const tex = "\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}";
  assert.equal(wrapMath(tex, true), `$$\n${tex}\n$$`);
});