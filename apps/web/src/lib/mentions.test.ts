/**
 * @引用 的解析规则。
 *
 *   node --experimental-strip-types --test src/lib/mentions.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  findMentionQuery,
  mentionBaseName,
  mentionCutRange,
  mentionDirName,
  mentionPaths,
  mentionSpanAfterCaret,
  mentionSpanBeforeCaret,
  parseMentions,
} from "./mentions.ts";

test("解析出普通引用", () => {
  assert.deepEqual(parseMentions("@README.md 看一下"), [
    { kind: "mention", text: "@README.md", path: "README.md", isDir: false, start: 0, end: 10 },
    { kind: "text", text: " 看一下" },
  ]);
});

test("结尾带斜杠的是文件夹", () => {
  assert.deepEqual(parseMentions("@apps/ 看看"), [
    { kind: "mention", text: "@apps/", path: "apps", isDir: true, start: 0, end: 6 },
    { kind: "text", text: " 看看" },
  ]);
});

test("带引号的文件夹也认得出斜杠", () => {
  assert.deepEqual(mentionPaths('@"我的 文件/"'), ["我的 文件"]);
  assert.deepEqual(parseMentions('@"我的 文件/"'), [
    { kind: "mention", text: '@"我的 文件/"', path: "我的 文件", isDir: true, start: 0, end: 9 },
  ]);
});

test("引用可以带目录", () => {
  assert.deepEqual(mentionPaths("看下 @apps/web/src/main.tsx 谢谢"), ["apps/web/src/main.tsx"]);
});

test("含空白的路径用引号包住", () => {
  assert.deepEqual(parseMentions('引用 @"我的 文件.md" 结尾'), [
    { kind: "text", text: "引用 " },
    { kind: "mention", text: '@"我的 文件.md"', path: "我的 文件.md", isDir: false, start: 3, end: 14 },
    { kind: "text", text: " 结尾" },
  ]);
});

test("两个引用之间的空格不会被吃掉", () => {
  assert.deepEqual(parseMentions("@a.md @b.md"), [
    { kind: "mention", text: "@a.md", path: "a.md", isDir: false, start: 0, end: 5 },
    { kind: "text", text: " " },
    { kind: "mention", text: "@b.md", path: "b.md", isDir: false, start: 6, end: 11 },
  ]);
});

test("词中间的 @ 只是普通字符", () => {
  assert.deepEqual(mentionPaths("user@example.com"), []);
});

test("没敲完的引号按普通文字处理", () => {
  assert.deepEqual(mentionPaths('引用 @"我的 文件'), []);
});

test("单独的 @ 不算引用", () => {
  assert.deepEqual(mentionPaths("@ "), []);
});

test("光秃秃的 @ 仍留在正文里", () => {
  // 输入框的高亮层靠这份解析画正文：把 @ 当成无效段丢掉，正文就会缺一块。
  assert.deepEqual(parseMentions("看下 @"), [
    { kind: "text", text: "看下 @" },
  ]);
  assert.deepEqual(parseMentions("@"), [{ kind: "text", text: "@" }]);
});

test("敲了一半的路径也算引用（敲的时候就要高亮）", () => {
  assert.deepEqual(parseMentions("看下 @READ"), [
    { kind: "text", text: "看下 " },
    { kind: "mention", text: "@READ", path: "READ", isDir: false, start: 3, end: 8 },
  ]);
});

test("定位光标前正在输入的引用", () => {
  const text = "看下 @READ";
  const found = findMentionQuery(text, text.length);
  assert.deepEqual(found, { start: 3, end: 8, query: "READ" });
});

test("@ 后面跟了空白就不再是引用", () => {
  const text = "看下 @README.md 再";
  assert.equal(findMentionQuery(text, text.length), null);
});

test("引号闭合后不再是引用", () => {
  const text = '看下 @"我的 文件.md"';
  assert.equal(findMentionQuery(text, text.length), null);
});

test("引号没闭合时仍算引用", () => {
  const text = '看下 @"我的文';
  assert.deepEqual(findMentionQuery(text, text.length), { start: 3, end: text.length, query: "我的文" });
});

test("词中间的 @ 不会触发选择器", () => {
  const text = "user@example.com";
  assert.equal(findMentionQuery(text, text.length), null);
});

test("光标不在引用里时不算", () => {
  const text = "看下 @README.md";
  assert.equal(findMentionQuery(text, 2), null);
});

test("取文件名和所在目录", () => {
  assert.equal(mentionBaseName("apps/web/src/main.tsx"), "main.tsx");
  assert.equal(mentionDirName("apps/web/src/main.tsx"), "apps/web/src");
  assert.equal(mentionDirName("README.md"), "");
});

test("退格时定位光标前面的整块引用", () => {
  // 选出来的 token 末尾带一个空格，光标就停在那儿 —— 一次删掉整块。
  const text = "@README.md ";
  assert.deepEqual(mentionSpanBeforeCaret(text, text.length), {
    start: 0,
    end: 10,
    path: "README.md",
  });
  // 光标停在引用末尾（还没吃掉空格）也算。
  assert.deepEqual(mentionSpanBeforeCaret(text, 10), { start: 0, end: 10, path: "README.md" });
  // 光标在引用中间：整块删。
  assert.deepEqual(mentionSpanBeforeCaret(text, 4), { start: 0, end: 10, path: "README.md" });
});

test("光标停在引用前面时不算（退格删的是前一个字）", () => {
  assert.equal(mentionSpanBeforeCaret("@README.md ", 0), null);
  assert.equal(mentionSpanBeforeCaret("看下 @README.md", 2), null);
});

test("离引用太远就不算整块删除", () => {
  assert.equal(mentionSpanBeforeCaret("@README.md 后面还有字", 13), null);
  assert.equal(mentionSpanBeforeCaret("普通文字", 4), null);
});

test("Delete 时定位光标后面的整块引用", () => {
  const text = "前面 @README.md 后面";
  assert.deepEqual(mentionSpanAfterCaret(text, 3), { start: 3, end: 13, path: "README.md" });
  // 光标已经在引用后面了，Delete 删的是后面的字。
  assert.equal(mentionSpanAfterCaret(text, 13), null);
});

test("整块删除时把末尾那个空格也带上", () => {
  const text = "@README.md ";
  const span = mentionSpanBeforeCaret(text, text.length);
  assert.ok(span);
  assert.deepEqual(mentionCutRange(text, span), { start: 0, end: 11 });
  // 引用后面没有空格时就只删引用本身。
  assert.deepEqual(mentionCutRange("@README.md", span), { start: 0, end: 10 });
});

test("光标落回已定型的引用里时，不再当成正在输入", () => {
  // 引用后面紧跟空白 —— 说明它已经定型了。这时候光标移回引用中间，
  // 选择器不该再弹出来，不然退格会变成逐字删，把 @文件名 拆成半截。
  const text = "@README.md ";
  assert.equal(findMentionQuery(text, 4), null);
  assert.equal(findMentionQuery(text, 10), null);
  // 敲到一半的路径（后面没有空白）还是要弹。
  assert.deepEqual(findMentionQuery("@READ", 5), { start: 0, end: 5, query: "READ" });
});
