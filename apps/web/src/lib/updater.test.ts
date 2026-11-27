/**
 * 更新状态机。这段逻辑是纯函数，不碰 window 也不碰主进程，所以能在 node 里直接跑。
 *
 * 值得盯住的是三条：
 * 1.「下载完」必须扛得住后面跟来的进度事件。electron-updater 在收尾阶段还会再
 *    吐几个 percent=100 的 progress 事件，要是照单全收，按钮会从「重启并安装」
 *    闪回「下载中 100%」，用户点了也没反应。
 * 2. 检查失败要能落到 error 而不是静默停在 checking —— 否则按钮永远转圈。
 * 3. 检查成功同样不能停在 checking，而且这里有个先后问题：事件一定比 IPC 回执
 *    先到（主进程先 emit 才 resolve）。下面第 8 个测试就是照真实顺序复现的。
 *    之前只测了纯 reducer、没测落地回执那一步，所以这个洞一直没人看见。
 *
 *   node --experimental-strip-types --test src/lib/updater.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyCheckResult,
  INITIAL_UPDATE_STATE,
  reduceUpdateEvent,
  type UpdateState,
} from "./updater.ts";

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

test("回归：事件先到、IPC 回执后到，迟到的回执不能把「有新版本」盖回 checking", () => {
  // 这是实测卡在「检查中…」的原因。真实顺序：主进程 checkForUpdates() 先 emit
  // update-available，再 resolve，于是 sendUpdateEvent 比 ipcMain.handle 的 return
  // 先到渲染进程。等回执回来时事件早已把状态定好了。
  const afterEvent = reduceUpdateEvent({ kind: "checking" }, { type: "available", version: "0.3.6" });

  // 老写法 setState(await checkForUpdate()) 的效果就是这一行：
  const naive = { kind: "checking" } as UpdateState;
  assert.deepEqual(naive, { kind: "checking" });
  assert.notDeepEqual(naive, afterEvent, "老写法确实会把正确结果抹掉，这就是 bug 本身");

  // 过了 applyCheckResult 之后：
  assert.deepEqual(applyCheckResult(afterEvent, { kind: "checking" }), {
    kind: "available",
    version: "0.3.6",
  });
});

test("回归：已是最新时也不能被回执盖回 checking", () => {
  const afterEvent = reduceUpdateEvent({ kind: "checking" }, { type: "latest", version: "0.3.6" });
  assert.deepEqual(applyCheckResult(afterEvent, { kind: "checking" }), {
    kind: "latest",
    version: "0.3.6",
  });
});

test("开发态：一个事件都不会有，回执是唯一真相，必须采纳", () => {
  assert.deepEqual(applyCheckResult({ kind: "checking" }, { kind: "unsupported" }), { kind: "unsupported" });
});

test("事件已经报过错，就别用回执里那句笼统的「检查失败」盖掉真实原因", () => {
  const afterEvent = reduceUpdateEvent({ kind: "checking" }, { type: "error", message: "getaddrinfo ENOTFOUND" });
  assert.deepEqual(applyCheckResult(afterEvent, { kind: "error", message: "检查失败" }), {
    kind: "error",
    message: "getaddrinfo ENOTFOUND",
  });
});

test("从「有新版本」点重新检查：先乐观回到 checking，事件到了仍能改写", () => {
  const start: UpdateState = { kind: "checking" };
  assert.deepEqual(applyCheckResult(start, { kind: "checking" }), { kind: "checking" });
  assert.deepEqual(reduceUpdateEvent(start, { type: "latest", version: "0.3.6" }), {
    kind: "latest",
    version: "0.3.6",
  });
  assert.deepEqual(reduceUpdateEvent(start, { type: "available", version: "0.3.7" }), {
    kind: "available",
    version: "0.3.7",
  });
});