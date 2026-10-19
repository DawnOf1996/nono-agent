/**
 * abort-marker.ts 的测试 —— 用 node --experimental-strip-types 直接跑：
 *   node --experimental-strip-types --test src/lib/abort-marker.test.ts
 *
 * 单独成文件正是为了让这类逻辑能被测到：sse.ts 挂着 `@/` 别名依赖，
 * node 的 test runner 解析不了，逻辑放那儿就等于没有测试。
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { ABORT_MARKER, stripAbortMarker } from "./abort-marker.ts";

test("正常回复原样返回，不算中止", () => {
  const result = stripAbortMarker("这是完整的回答。");
  assert.equal(result.aborted, false);
  assert.equal(result.text, "这是完整的回答。");
});

test("含中止标记时摘掉标记正文，标为 aborted", () => {
  const result = stripAbortMarker(`我将开始测试。${ABORT_MARKER}`);
  assert.equal(result.aborted, true);
  assert.equal(result.text, "我将开始测试。");
});

test("摘完不留空行", () => {
  const result = stripAbortMarker(`步骤 1\n${ABORT_MARKER}`);
  assert.equal(result.text, "步骤 1");
});

test("正文里恰好只有标记时，摘完是空串但仍算中止", () => {
  const result = stripAbortMarker(ABORT_MARKER);
  assert.equal(result.aborted, true);
  assert.equal(result.text, "");
});

test("空串不算中止", () => {
  const result = stripAbortMarker("");
  assert.equal(result.aborted, false);
  assert.equal(result.text, "");
});

test("标记是纯文本，不会把用户正文的相似措辞误判", () => {
  // 用户或模型完全可能自己写出"用户已取消"这几个字 —— 不能因此把整条回复标成中止。
  const result = stripAbortMarker("用户已取消本次请求是我自己的判断，不是你说的。");
  assert.equal(result.aborted, false);
  assert.equal(result.text, "用户已取消本次请求是我自己的判断，不是你说的。");
});
