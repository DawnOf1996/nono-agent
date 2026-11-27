/**
 * 流式分块的测试。
 *
 * 这里出过的真问题：$$ 块内部有空行时按空行切，公式被劈成两半，
 * 两半单独解析都渲染不出来。纯代码块围栏早就防住了，$$ 之前漏了。
 *
 *   node --experimental-strip-types --test src/lib/markdown-blocks.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { isUnclosedMathBlock, splitMarkdownBlocks } from "./markdown-blocks.ts";

test("空行分段", () => {
  assert.deepEqual(splitMarkdownBlocks("一\n\n二"), ["一", "二"]);
});

test("代码围栏内部有空行也不切", () => {
  const blocks = splitMarkdownBlocks("前\n\n```py\nx = 1\n\ny = 2\n```\n\n后");
  assert.deepEqual(blocks, ["前", "```py\nx = 1\n\ny = 2\n```", "后"]);
});

test("$$ 块内部有空行仍是一个块", () => {
  const source = ["前", "", "$$", "\\begin{aligned}", "a &= b", "", "c &= d", "\\end{aligned}", "$$", "", "后"].join("\n");
  const blocks = splitMarkdownBlocks(source);
  assert.equal(blocks.length, 3, `应该切成 3 块，实际 ${blocks.length}：${JSON.stringify(blocks)}`);
  assert.ok(blocks[1]?.startsWith("$$") && blocks[1]?.endsWith("$$"), "中间那块要完整包住公式");
  assert.equal(blocks[0], "前");
  assert.equal(blocks[2], "后");
});

test("$$ 块不会和后面的正文粘在一起", () => {
  const blocks = splitMarkdownBlocks("$$\nx\n$$\n\n后面");
  assert.deepEqual(blocks, ["$$\nx\n$$", "后面"]);
});

test("$$ 里的 ``` 不会把公式块劈开", () => {
  // verbatim 环境带 ```，先判围栏会当场开一个代码块。
  const source = ["$$", "\\begin{verbatim}", "```", "\\end{verbatim}", "$$"].join("\n");
  assert.deepEqual(splitMarkdownBlocks(source), [source]);
});

test("普通围栏照旧按代码块处理，不被 $$ 逻辑带偏", () => {
  const blocks = splitMarkdownBlocks("```ts\nconst a = 1;\n```");
  assert.deepEqual(blocks, ["```ts\nconst a = 1;\n```"]);
});

test("行内 $ 不影响分块", () => {
  assert.deepEqual(splitMarkdownBlocks("质能方程 $E=mc^2$ 这条"), ["质能方程 $E=mc^2$ 这条"]);
});

test("列表续段并回上一块", () => {
  assert.deepEqual(splitMarkdownBlocks("- 一\n- 二"), ["- 一\n- 二"]);
});

test("未闭合的 $$ 判为待定稿", () => {
  assert.equal(isUnclosedMathBlock("$$\n\\frac{a}"), true);
  assert.equal(isUnclosedMathBlock("$$\n\\frac{a}\n$$"), false);
  assert.equal(isUnclosedMathBlock("没有公式"), false);
  // 两对 $$ 闭合了就不是待定稿。
  assert.equal(isUnclosedMathBlock("$$\na\n$$\n\n$$\nb\n$$"), false);
});