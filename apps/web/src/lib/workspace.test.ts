/**
 * 工作区路径与 @引用 token 的拼法。
 *
 *   node --experimental-strip-types --test src/lib/workspace.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { mentionToken, relativeTo } from "./workspace.ts";

const ROOT = "D:\\WorkSpace\\Projects\\MyProjects\\nono-agent";

test("token 是相对路径，并且末尾自带一个空格", () => {
  // 末尾这个空格很要紧：引用和后面的正文靠它分开，
  // 光标也正好停在它后面，用户接着打字就是「@README.md 正文」。
  assert.equal(mentionToken(ROOT, `${ROOT}\\README.md`), "@README.md ");
  assert.equal(mentionToken(ROOT, `${ROOT}\\apps\\web\\src\\main.tsx`), "@apps/web/src/main.tsx ");
});

test("文件夹的 token 结尾带斜杠", () => {
  // 目录没有预览页，靠结尾的 / 才能把它认成目录（见 lib/mentions.ts 的解析）。
  assert.equal(mentionToken(ROOT, `${ROOT}\\apps`, true), "@apps/ ");
});

test("路径里有空白就加引号", () => {
  assert.equal(mentionToken(ROOT, `${ROOT}\\我的 文件.md`), '@"我的 文件.md" ');
});

test("不在工作区里的路径原样保留", () => {
  // 反斜杠统一成正斜杠：token 里的路径分隔符只有一种写法，解析才不会漏。
  assert.equal(mentionToken(ROOT, "D:\\Other\\README.md"), "@D:/Other/README.md ");
});

test("相对路径按工作区根算", () => {
  assert.equal(relativeTo(ROOT, `${ROOT}\\apps\\web`), "apps\\web");
  assert.equal(relativeTo(ROOT, "D:\\Other"), "D:\\Other");
});
