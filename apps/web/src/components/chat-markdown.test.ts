/**
 * 聊天区 Markdown 管线的回归测试。
 *
 * 聊天侧不跑 rehype-sanitize（消息不是仓库文件），插件链比文件预览短一截：
 *   remarkGfm → remarkMath → rehypeKatex
 *
 * components 表（AlertBlock / CodeFence / 链接）都在 MarkdownText.tsx 里，是 JSX，
 * node 跑不了，所以这里只覆盖插件链的净效果。组件接线靠 tsc 把关。
 *
 *   node --experimental-strip-types --test src/components/chat-markdown.test.ts
 */
import assert from "node:assert/strict";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { test } from "node:test";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

import { KATEX_OPTIONS } from "../lib/katex.ts";

/** 完整渲染：MarkdownText 正常态走的就是这条链。 */
function render(md: string): string {
  return renderToStaticMarkup(
    h(Markdown, {
      remarkPlugins: [remarkGfm, remarkMath],
      rehypePlugins: [[rehypeKatex, KATEX_OPTIONS]],
    }, md),
  );
}

/** 流式途中未闭合的 $$ 块：MarkdownChunk 会把 rehypePlugins 摘掉。 */
function renderDeferredMath(md: string): string {
  return renderToStaticMarkup(h(Markdown, { remarkPlugins: [remarkGfm, remarkMath] }, md));
}

const count = (html: string, needle: string): number => html.split(needle).length - 1;

test("行内公式在聊天里也渲染", () => {
  const html = render("质能方程是 $E = mc^2$ 这条。");
  assert.ok(count(html, 'class="katex"') > 0, "应该有 katex 节点");
  assert.equal(count(html, "<code"), 0, "行内公式不该留下 <code> 外壳");
});

test("块级公式在聊天里也渲染", () => {
  const html = render("$$\n\\frac{a}{b}\n$$");
  assert.ok(count(html, "katex-display") > 0, "块级公式应该有 katex-display");
});

test("```math 围栏走公式", () => {
  const html = render("```math\n\\lim_{n \\to \\infty} x\n```");
  assert.ok(count(html, "katex-display") > 0);
});

test("代码围栏的高亮不受公式逻辑影响", () => {
  const html = render("```ts\nconst a = 1;\n```");
  assert.ok(html.includes('class="language-ts"'), "语言名必须活着");
  assert.equal(count(html, 'class="katex'), 0, "普通围栏不该被当成公式");
});

test("公式里的 \\href 拿不到链接（trust 关着）", () => {
  const html = render("$\\href{https://evil.example}{点我}$");
  assert.equal(html.includes('href="https://evil.example"'), false, "\\href 不该产出真实链接");
  assert.equal(html.includes("<a "), false);
});

test("写错的公式不炸整条消息", () => {
  const html = render("前面一句。\n\n$$\n\\frac{1}{\n$$\n\n后面一句。");
  assert.ok(html.includes("前面一句。") && html.includes("后面一句。"), "坏公式不能把整条吞掉");
});

test("流式中未闭合的行内 $ 按普通文字渲染，不会闪红字", () => {
  // markdown-blocks.ts 的注释里断言了这一点：micromark 收不到收尾的 $ 时
  // 会把整段当文字，等 $ 到了才升级成公式。这条把那个断言钉住。
  const html = render("$E = mc");
  assert.equal(count(html, 'class="katex'), 0, "没闭合就不该渲染成公式");
  assert.ok(html.includes("$E = mc"), "应该原样显示成文字");
});

test("流式中未闭合的 $$ 块摘掉 rehypeKatex 后按源码显示", () => {
  const partial = "$$\n\\begin{aligned}\na &= b";
  assert.equal(count(renderDeferredMath(partial), 'class="katex'), 0, "半截公式不该送进 KaTeX");
  // 闭合之后同一个块就该正常渲染了。
  assert.ok(count(render(`${partial}\n\\end{aligned}\n$$`), "katex-display") > 0, "闭合后要能渲染");
});