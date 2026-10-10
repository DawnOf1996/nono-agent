/**
 * 文件预览 Markdown 管线的回归测试。
 *
 * 为什么用整条管线测而不是只测纯函数：alert 那一版就是在纯函数测试全绿的情况下
 * 坏的 —— 测试自己拼的 children 树和 react-markdown 真正发下来的不一样
 * （块之间夹着 "\n" 分隔节点）。凡是"渲染出来什么样"的担心，都得真跑一遍。
 *
 * 这里不覆盖 components 表（那是 JSX），只覆盖插件链和白名单的净效果：
 *   remarkGfm → remarkMath → rehypeRaw → rehypeSanitize → rehypeKatex
 *
 *   node --experimental-strip-types --test src/features/files/preview/markdown/pipeline.test.ts
 */
import assert from "node:assert/strict";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { test } from "node:test";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";

import { KATEX_OPTIONS } from "../../../../lib/katex.ts";

import { remarkLineNumbers } from "./remark-lines.ts";
import { rehypeMathLines } from "./rehype-math-lines.ts";
import { markdownSchema } from "./schema.ts";

/** 和 MarkdownBody 用的是同一套插件顺序与选项。 */
function render(md: string): string {
  return renderToStaticMarkup(
    h(Markdown, {
      remarkPlugins: [remarkGfm, remarkMath, remarkLineNumbers],
      rehypePlugins: [rehypeRaw, [rehypeSanitize, markdownSchema], [rehypeMathLines, KATEX_OPTIONS]],
    }, md),
  );
}

const count = (html: string, needle: string): number => html.split(needle).length - 1;

test("行内公式渲染成 katex，源码不留在页面上", () => {
  const html = render("质能方程是 $E = mc^2$ 这条。");
  assert.ok(count(html, 'class="katex"') > 0, "应该有 katex 节点");
  assert.equal(count(html, "<code"), 0, "行内公式不该留下 <code> 外壳");
  // MathML 的 <annotation> 里本来就存着 TeX 源码（无障碍 + 复制），不算漏渲染。
  assert.equal(html.replace(/<annotation[\s\S]*?<\/annotation>/g, "").includes("$E"), false);
});

test("块级公式带 katex-display", () => {
  const html = render("$$\n\\frac{a}{b}\n$$");
  assert.ok(count(html, "katex-display") > 0, "块级公式应该有 katex-display");
});

test("\\text 里的中文正常出字", () => {
  const html = render("$$\n\\underbrace{a+b}_{\\text{下标说明}}\n$$");
  assert.ok(html.includes("下标说明"), "\\text 里的中文应该渲染出来");
});

test("矩阵 / 方程组这类环境能过", () => {
  const html = render("$$\n\\begin{bmatrix} a & b \\\\ c & d \\end{bmatrix}\n$$");
  assert.ok(html.includes("bmatrix"), "bmatrix 环境应该在");
});

test("```math 围栏也走公式", () => {
  const html = render("```math\n\\lim_{n \\to \\infty} x\n```");
  assert.ok(count(html, "katex-display") > 0, "```math 围栏应该渲染成块级公式");
});

test("写错的公式不炸整篇，预览照常出内容", () => {
  const html = render("前面一句。\n\n$$\n\\frac{1}{\n$$\n\n后面一句。");
  assert.ok(html.includes("前面一句。") && html.includes("后面一句。"), "坏公式不能把整篇吞掉");
});

test("普通代码围栏的高亮不能被公式逻辑打掉", () => {
  const html = render("```ts\nconst a = 1;\n```");
  assert.ok(html.includes('class="language-ts"'), "围栏语言名必须活着，否则 CodeFence 回落成 text");
  assert.equal(count(html, 'class="katex'), 0, "普通围栏不该被当成公式");
});

test("代码围栏里的 $ 不渲染", () => {
  const html = render("```\n$x$\n```");
  assert.equal(count(html, 'class="katex'), 0, "围栏里的 $ 只是文字");
});

test("GFM 任务列表的 class 不能被白名单吞掉", () => {
  // defaultSchema 特意给 ul/li 留了 contains-task-list / task-list-item，
  // 写死白名单的那版把它们弄丢了。
  const html = render("- [ ] 待办\n- [x] 完成");
  assert.ok(html.includes("task-list-item"), "任务列表项的 class 要保住");
  assert.ok(html.includes("contains-task-list"), "任务列表容器的 class 要保住");
});

test("公式里的 \\href 被拦下，不会变成可点的链接", () => {
  // rehypeKatex 排在 sanitize 之后，输出不过白名单，全靠 trust: false 兜着。
  // 这条就是那道闸的回归测试 —— trust 一旦被改成 true，下面这个 href 属性就会冒出来。
  //
  // 注意别去断言页面上"没有 evil.example 这个字符串"：trust 关着时 KaTeX 是把命令和
  // 参数当普通数学文字画出来（红色报错样式），URL 会以字符形式出现在正文里，
  // 那不是链接。真正要守住的是"没有 href 属性指向它"。
  const html = render("$\\href{https://evil.example}{点我}$");
  assert.equal(html.includes('href="https://evil.example"'), false, "\\href 不该产出真实链接");
  assert.equal(html.includes("<a "), false, "不该凭空多出 <a> 元素");
});

test("行内 HTML 的 event handler 仍然被剥掉（sanitize 没被绕过）", () => {
  const html = render('<img src="x" onerror="alert(1)">');
  assert.equal(html.includes("onerror"), false, "原始 HTML 的事件属性必须被 sanitize 剥掉");
});

test("公式上的行号要还回 katex 根节点，否则引用没有行号", () => {
  // rehype-katex 整体替换 <code>，data-line 会跟着丢；rehypeMathLines 负责补回来。
  // SelectionQuote.lineOf 靠最近的 [data-line] 祖先换算源文件行号，缺了就只能给 null。
  const html = render(["前面一句。", "", "$$", "E = mc^2", "$$", "", "行内 $a+b$ 在这里"].join("\n"));
  // $$ 在第 3 行（1 起），行内公式在第 7 行那一段里。
  assert.ok(
    /<span class="katex" data-line="3"/.test(html) || /data-line="3"[^>]*class="katex"/.test(html),
    `块级公式的 katex 根节点上应该有 data-line="3"，实际：${html.slice(html.indexOf("katex-display"), html.indexOf("katex-display") + 120)}`,
  );
  assert.ok(html.includes('data-line="7"'), "行内公式那段的行号也要在");
});

test("没有行号的公式不会被塞一个假行号", () => {
  const html = render("$$E=mc^2$$");
  assert.ok(count(html, 'class="katex"') > 0, "照样要渲染");
  assert.equal(html.includes("data-line=\"undefined\""), false, "绝不能把 undefined 写进行号");
});

test("行号要真的落到 DOM 的 data-line 属性上", () => {
  // 白名单里写的必须是 hast 属性名 dataLine，落到 DOM 上才是 data-line。
  // 写成 data-line 的话 sanitize 会整条剥掉，页面上一个 [data-line] 都没有 ——
  // 引用只有内容没有行号，而且页面看上去一切正常，很难发现。这条就是防它再溜回来。
  const html = render(["一", "", "二"].join("\n"));
  assert.ok(html.includes('data-line="1"'), `第一段要有 data-line="1"，实际：${html}`);
  assert.ok(html.includes('data-line="3"'), `第二段要有 data-line="3"，实际：${html}`);
});