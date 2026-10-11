/**
 * toast 的 store 层。这段不碰 React，只有模块级状态进出，所以能直接跑：
 *
 *   node --experimental-strip-types --test src/lib/toast.test.ts
 *
 * 值得盯住的是最后一条：getToasts() 的返回值会喂给 useSyncExternalStore，
 * 它要求「内容没变就必须是同一个引用」。一旦每次都造新数组，React 会认为快照
 * 一直在变并无限重渲染 —— 而且这种死循环在 UI 上表现为白屏或卡死，很难往这个
 * 函数上想。所以这条断言是这里最值钱的。
 */
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  dismissToast,
  getToastAnchor,
  getToasts,
  registerToastAnchor,
  subscribeToasts,
  toast,
} from "./toast.ts";

// store 是模块级状态，不隔离就会互相污染。每个用例开始前清干净。
let anchorCleanups: Array<() => void> = [];
beforeEach(() => {
  for (const item of getToasts()) dismissToast(item.id);
  for (const off of anchorCleanups) off();
  anchorCleanups = [];
});

// 测试跑在 node 里没有 DOM，而锚点栈只存引用不碰元素属性，
// 所以拿空对象冒充 HTMLElement 就够了。
function anchor(): HTMLElement {
  const element = {} as HTMLElement;
  anchorCleanups.push(registerToastAnchor(element));
  return element;
}

test("toast 把消息放进列表,dismissToast 再摘出来", () => {
  const id = toast("已是最新版本 0.3.6");
  assert.equal(getToasts().length, 1);
  assert.deepEqual(getToasts()[0], { id, message: "已是最新版本 0.3.6" });
  dismissToast(id);
  assert.deepEqual(getToasts(), []);
});

test("多条按发出顺序堆叠", () => {
  const first = toast("第一条");
  const second = toast("第二条");
  assert.deepEqual(
    getToasts().map((item) => item.message),
    ["第一条", "第二条"],
  );
  assert.notEqual(first, second, "id 必须互不相同，否则 React key 会撞");
});

test("到时长自己消失", async () => {
  toast("会自己消失", { duration: 10 });
  assert.equal(getToasts().length, 1);
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.deepEqual(getToasts(), []);
});

test("对同一个 id 重复 dismiss 是幂等的,不会多触发一次通知", () => {
  const id = toast("只该通知一次");
  let notifications = 0;
  const unsubscribe = subscribeToasts(() => {
    notifications += 1;
  });
  dismissToast(id);
  const afterFirst = notifications;
  dismissToast(id);
  assert.equal(notifications, afterFirst);
  unsubscribe();
});

test("退订之后不再收到通知", () => {
  let notifications = 0;
  const unsubscribe = subscribeToasts(() => {
    notifications += 1;
  });
  const id = toast("退订前");
  unsubscribe();
  const seen = notifications;
  dismissToast(id);
  assert.equal(notifications, seen, "退订之后不该再收到通知");
});

test("getToasts 在内容没变时返回同一个引用", () => {
  toast("引用稳定性");
  const snapshot = getToasts();
  assert.equal(getToasts(), snapshot, "快照换引用会被 useSyncExternalStore 判成一直在变");
  dismissToast(snapshot[0].id);
  assert.notEqual(getToasts(), snapshot, "真变了就该换新引用，否则 UI 不更新");
});

test("没人登记锚点时是 null,调用方据此退回整窗", () => {
  assert.equal(getToastAnchor(), null);
});

test("登记过的容器就是 toast 的落点", () => {
  const panel = anchor();
  assert.equal(getToastAnchor(), panel);
});

test("锚点按栈取顶：套起来的弹框,后开的那个在上", () => {
  const settings = anchor();
  const confirm = anchor();
  assert.equal(getToastAnchor(), confirm, "后来者盖在先来者上面");
  assert.notEqual(getToastAnchor(), settings);
});

test("退掉栈顶之后回到上一个,而不是掉回整窗", () => {
  const settings = {} as HTMLElement;
  anchorCleanups.push(registerToastAnchor(settings));
  const confirm = {} as HTMLElement;
  const offConfirm = registerToastAnchor(confirm);
  anchorCleanups.push(offConfirm);
  assert.equal(getToastAnchor(), confirm);

  offConfirm();
  assert.equal(getToastAnchor(), settings, "栈顶撤掉后应回到上一层");
  assert.notEqual(getToastAnchor(), null, "settings 还在栈里，不该退回整窗");
});

test("重复登记同一个元素不会让它变成多份", () => {
  const element = {} as HTMLElement;
  const offA = registerToastAnchor(element);
  anchorCleanups.push(registerToastAnchor(element));
  assert.equal(getToastAnchor(), element);
  offA();
  assert.equal(getToastAnchor(), null, "第一次退订就把这个元素整个摘掉了");
});