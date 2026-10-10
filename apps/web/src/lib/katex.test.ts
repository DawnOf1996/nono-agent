/**
 * KaTeX 吐 HTML 的那份，和引 CSS 的那份，必须是同一个。
 *
 * 背景：真正渲染公式的是 rehype-katex 自己依赖的那份 katex（rehype-katex@7 依赖
 * katex@^0.16），而 package.json 里直接依赖的 katex 只被 index.css 拿去
 * @import katex.min.css。这两份一旦不是同一个版本，就出现一种特别难查的坏法：
 *
 *   不报错、不白屏、绝大部分公式完全正常，只有少数命令悄悄画错。
 *   \boxed               → 空壳 span 丢了 width:100%，边框塌成一根竖线（E = |mc²）
 *   \overline / \underline → 横线直接不画
 *   \frac / \underbrace … → 用的 class 恰好没改名，所以看着一点问题都没有
 *
 * 肉眼排查得逐条命令比对才发现，所以在这里钉死。
 *
 *   node --experimental-strip-types --test src/lib/katex.test.ts
 */
import assert from "node:assert/strict";
import { readFileSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { test } from "node:test";

import { KATEX_OPTIONS } from "./katex.ts";

const require = createRequire(import.meta.url);

/** rehype-katex 的入口。 */
const rehypeKatexEntry = require.resolve("rehype-katex");

/** rehype-katex 里 `import katex from "katex"` 解析到的那份 —— 页面上的 HTML 就是它吐的。 */
const rendererEntry = require.resolve("katex", { paths: [rehypeKatexEntry] });

/** index.css 里 @import 的那份 —— 页面上生效的 CSS 就是它的 katex.min.css。 */
const styleEntry = require.resolve("katex/package.json");

const katexCssPath = join(dirname(styleEntry), "dist", "katex.min.css");
const katexCss = readFileSync(katexCssPath, "utf8");

/**
 * 样式表里出现过的选择器片段。@media / @font-face 这类 at-rule 前言也会被收进来，
 * 判据宽松一点不会漏放真问题，这里不追求精确解析。
 */
const cssSelectors = [...katexCss.matchAll(/([^{}]+)\{[^{}]*\}/g)].map((match) => match[1]);

const cssStyles = (className: string): boolean => cssSelectors.some((selector) => selector.includes(`.${className}`));

/** 和 rehype-katex 一样地调它那份 katex，省掉整条管线的开销。 */
function renderAsRehypeKatex(tex: string): string {
  const renderer = require(rendererEntry) as typeof import("katex");
  return renderer.renderToString(tex, { ...KATEX_OPTIONS, displayMode: true, throwOnError: true });
}

function classesOf(html: string): string[] {
  const classes = new Set<string>();
  for (const match of html.matchAll(/class="([^"]+)"/g)) {
    for (const className of match[1].split(/\s+/).filter(Boolean)) classes.add(className);
  }
  return [...classes];
}

/**
 * 覆盖容易踩的几类命令，尤其是曾经因为版本错配坏掉的那几个。
 * 新增公式时留意一下：只有"确实会碰到底层排版"的命令才值得加，纯文本命令价值不大。
 */
const FORMULAS = [
  "\\boxed{E = mc^2}",
  "\\overline{AB}",
  "\\underline{x}",
  "\\frac{a}{b}",
  "\\sqrt{x^2 + y^2}",
  "\\underbrace{a + b + c}_{t}",
  "\\overbrace{x^2 + y^2}^{t}",
  "\\overrightarrow{AB}",
  "\\begin{bmatrix} a & b \\\\ c & d \\end{bmatrix}",
  "\\begin{array}{c} a \\\\ b \\end{array}",
  "\\sum_{i=1}^{n} i",
  "\\int_0^1 f(x)\\,dx",
  "\\lim_{x \\to 0} \\frac{\\sin x}{x}",
  "a \\cdot b \\pm c",
  "\\left\\{ \\frac{a}{b} \\right\\}",
  "\\text{中文} \\quad \\mathbf{v} \\quad \\mathbb{R}",
];

/**
 * KaTeX 会打一些纯结构钩子的 class，它们在 KaTeX 自家 CSS 里也没有规则，不是错配。
 * 只有确认过"这个 class 本来就不参与样式"才能加进来 —— 加之前先去 katex.min.css 里搜一下。
 */
const UNSTYLED_HOOKS = new Set(["mbin", "mclose", "minner", "mop", "mopen", "mord", "mrel", "mtight", "cjk_fallback"]);

test("渲染公式的 katex 和引 CSS 的 katex 必须是同一个", () => {
  const rendered = require(require.resolve("katex/package.json", { paths: [rehypeKatexEntry] })).version as string;
  const styled = require(styleEntry).version as string;

  assert.equal(
    rendered,
    styled,
    `rehype-katex 用 katex@${rendered} 渲染，index.css 引的是 katex@${styled} 的 CSS。` +
      `两份的 class 命名对不上，\\boxed / \\overline / \\underline 会静默画错。` +
      `对齐办法：把 package.json 里的 katex 钉到 ^${rendered}。`,
  );
});

test("两份 katex 应该被去重成同一个物理副本", () => {
  // 同一份装两份不只是慢：字体文件会按两套 @font-face 各加载一遍，
  // 而且下次谁动了其中一个版本，这个 bug 就悄悄回来了。
  const renderedPkg = require.resolve("katex/package.json", { paths: [rehypeKatexEntry] });
  assert.equal(
    realpathSync(renderedPkg),
    realpathSync(styleEntry),
    "rehype-katex 和 @nono/web 解析到的不是同一份 katex，检查一下 lockfile 里的版本声明",
  );
});

test("\\boxed / \\overline / \\underline 依赖的排版规则必须真的在 CSS 里", () => {
  // 定点回归：\boxed 的框是空壳 span，全靠 .stretchy 的 width:100% 撑出宽度，
  // 少了这条规则它就塌成 0 宽，只剩 0.04em 的边框画成一根竖线。报错信息里
  // 写成"竖线"而不是"没边框"，是因为那个 0.04em 的内联 border 还在。
  const cases: Array<[string, string]> = [
    ["\\boxed{E = mc^2}", "stretchy"],
    ["\\overline{AB}", "overline"],
    ["\\underline{x}", "underline"],
  ];

  for (const [tex, required] of cases) {
    const classes = classesOf(renderAsRehypeKatex(tex));
    assert.ok(classes.includes(required), `${tex} 应该渲染出 .${required}，实际渲染出：${classes.join(" ")}`);
    assert.ok(cssStyles(required), `katex.min.css 里没有 .${required} 规则，${tex} 会画错`);
  }
});

test("公式吐出来的每个 class，都能在 katex.min.css 里找到对应的规则", () => {
  const emitted = new Set<string>();
  for (const tex of FORMULAS) {
    for (const className of classesOf(renderAsRehypeKatex(tex))) emitted.add(className);
  }

  const orphans = [...emitted].filter((className) => !cssStyles(className) && !UNSTYLED_HOOKS.has(className));
  assert.deepEqual(
    orphans,
    [],
    `这些 class 在 ${katexCssPath} 里没有对应规则，对应样式会整块失效。` +
      `要么 katex 的 JS 和 CSS 又错配了，要么 UNSTYLED_HOOKS 白名单该更新了。`,
  );
});
