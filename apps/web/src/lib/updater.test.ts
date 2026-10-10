/**
 * 更新状态机。这段逻辑是纯函数，不碰 window 也不碰主进程，所以能在 node 里直接跑。
 *
 * 值得盯住的是两条：
 * 1.「下载完」必须扛得住后面跟来的进度事件。electron-updater 在收尾阶段还会再
 *    吐几个 percent=100 的 progress 事件，要是照单全收，按钮会从「重启并安装」
 *    闪回「下载中 100%」，用户点了也没反应。
 * 2. 检查失败要能落到 error 而不是静默停在 checking —— 否则按钮永远转圈。
 *
 *   node --experimental-strip-types --test src/lib/updater.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { INITIAL_UPDATE_STATE, reduceUpdateEvent, type UpdateState } from "./updater.ts";

function fold(events: NonoUpdateEvent[], from: UpdateState = INITIAL_UPDATE_STATE): UpdateState {
  return events.reduce(reduceUpdateEvent, from);
}

test("checking 把状态切到 checking", () => {
  assert.deepEqual(reduceUpdateEvent(INITIAL_UPDATE_STATE, { type: "checking" }), { kind: "checking" });
});

test("available 带回新版本号", () => {
  const next = reduceUpdateEvent({ kind: "checking" }, { type: "available", version: "0.3.0" });
  assert.deepEqual(next, { kind: "available", version: "0.3.0" });
});

test("latest 允许 version 为空", () => {
  // 主进程那侧 info.version 是从 latest.yml 读的，文件结构不对时可能拿不到值。
  // 那也应该如实显示「已是最新」，而不是抛给调用方。
  assert.deepEqual(reduceUpdateEvent({ kind: "checking" }, { type: "latest", version: null }), {
    kind: "latest",
    version: null,
  });
});

test("progress 累积成 downloading", () => {
  const next = fold([{ type: "checking" }, { type: "available", version: "0.3.0" }, { type: "progress", percent: 42, transferred: 1, total: 2 }]);
  assert.deepEqual(next, { kind: "downloading", percent: 42 });
});

test("下载完后到达的进度事件不会把状态打回去", () => {
  // 回归：electron-updater 收尾时还会补几个 progress，percent 恰好是 100。
  const downloaded = fold([
    { type: "checking" },
    { type: "available", version: "0.3.0" },
    { type: "progress", percent: 99, transferred: 1, total: 2 },
    { type: "downloaded", version: "0.3.0" },
  ]);
  assert.deepEqual(downloaded, { kind: "downloaded", version: "0.3.0" });

  const after = reduceUpdateEvent(downloaded, { type: "progress", percent: 100, transferred: 2, total: 2 });
  assert.deepEqual(after, { kind: "downloaded", version: "0.3.0" });
});

test("error 落到 error 并带上原因", () => {
  const next = reduceUpdateEvent({ kind: "checking" }, { type: "error", message: "getaddrinfo ENOTFOUND" });
  assert.deepEqual(next, { kind: "error", message: "getaddrinfo ENOTFOUND" });
});

test("整条真实链路：查 → 有新版 → 下载 → 下完", () => {
  const next = fold([
    { type: "checking" },
    { type: "available", version: "0.2.2026101013" },
    { type: "progress", percent: 0, transferred: 0, total: 1 },
    { type: "progress", percent: 50, transferred: 1, total: 2 },
    { type: "progress", percent: 100, transferred: 2, total: 2 },
    { type: "downloaded", version: "0.2.2026101013" },
  ]);
  assert.deepEqual(next, { kind: "downloaded", version: "0.2.2026101013" });
});