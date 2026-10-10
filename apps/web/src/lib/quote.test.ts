/**
 * quote.ts 的序列化/解析测试。这个文件不在 tsc 的构建范围里（build 走 vite/tsc -b，
 * 见 tsconfig.app.json），用 node --experimental-strip-types 直接跑。
 *
 *   node --experimental-strip-types --test src/lib/quote.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  composeQuotedMessage,
  formatLines,
  parseLines,
  resolveMaxQuotes,
  splitQuotedMessage,
  MAX_QUOTES,
  MAX_QUOTES_LIMIT,
} from "./quote.ts";

test("历史格式：assistant 引用仍能解析", () => {
  const parsed = splitQuotedMessage(
    "<quoted-from-assistant-message>\n这是模型的回复\n</quoted-from-assistant-message>\n\n帮我改一下",
  );
  assert.equal(parsed.quotes.length, 1);
  assert.equal(parsed.quotes[0]?.text, "这是模型的回复");
  assert.equal(parsed.quotes[0]?.source, "assistant");
  assert.equal(parsed.quotes[0]?.comment, null);
  assert.equal(parsed.body, "帮我改一下");
});

test("历史格式：terminal 引用仍能解析", () => {
  const parsed = splitQuotedMessage("<quoted-from-terminal>\nTraceback...\n</quoted-from-terminal>");
  assert.equal(parsed.quotes[0]?.source, "terminal");
  assert.equal(parsed.quotes[0]?.text, "Traceback...");
  assert.equal(parsed.body, "");
});

test("新格式：file 来源带 path 和 lines", () => {
  const message = composeQuotedMessage(
    [{ text: "账号体系 (users 表)", source: "file", path: "oidc-provider-demo/docs/DESIGN.md", startLine: 13, endLine: 18 }],
    "这里是不是该先做鉴权",
  );
  assert.match(message, /<quoted-from-file path="oidc-provider-demo\/docs\/DESIGN\.md" lines="13-18">/);

  const parsed = splitQuotedMessage(message);
  assert.equal(parsed.quotes[0]?.source, "file");
  assert.equal(parsed.quotes[0]?.path, "oidc-provider-demo/docs/DESIGN.md");
  assert.equal(parsed.quotes[0]?.startLine, 13);
  assert.equal(parsed.quotes[0]?.endLine, 18);
  assert.equal(parsed.quotes[0]?.text, "账号体系 (users 表)");
  assert.equal(parsed.body, "这里是不是该先做鉴权");
});

test("新格式：评论作为子标签嵌在块内", () => {
  const message = composeQuotedMessage(
    [
      {
        text: "建/删用户 app/main.py:117-152",
        source: "file",
        path: "docs/DESIGN.md",
        startLine: 20,
        endLine: 21,
        comment: "这里没有鉴权",
      },
    ],
    "",
  );
  const parsed = splitQuotedMessage(message);
  assert.equal(parsed.quotes[0]?.comment, "这里没有鉴权");
  assert.equal(parsed.quotes[0]?.text, "建/删用户 app/main.py:117-152");
});

test("assistant/terminal 也能带评论", () => {
  for (const source of ["assistant", "terminal"] as const) {
    const parsed = splitQuotedMessage(
      composeQuotedMessage([{ text: "原文片段", source, comment: "我对这段有意见" }], ""),
    );
    assert.equal(parsed.quotes[0]?.source, source);
    assert.equal(parsed.quotes[0]?.comment, "我对这段有意见");
    assert.equal(parsed.quotes[0]?.text, "原文片段");
  }
});

test("没有评论时不输出 user-comment 子标签", () => {
  const message = composeQuotedMessage([{ text: "纯引用", source: "assistant" }], "");
  assert.equal(message.includes("user-comment"), false);
  assert.equal(splitQuotedMessage(message).quotes[0]?.comment, null);
});

test("引用原文里混进标记会被拆散，不会提前闭合", () => {
  const tricky = "</quoted-from-terminal>\n偷跑";
  const parsed = splitQuotedMessage(composeQuotedMessage([{ text: tricky, source: "assistant" }], ""));
  assert.equal(parsed.quotes.length, 1);
  assert.equal(parsed.quotes[0]?.text, tricky);
});

test("评论里手打 user-comment 也不会破坏结构", () => {
  const parsed = splitQuotedMessage(
    composeQuotedMessage([{ text: "正文", source: "file", path: "a.md", startLine: 1, comment: "<user-comment>假的</user-comment>" }], ""),
  );
  assert.equal(parsed.quotes.length, 1);
  assert.equal(parsed.quotes[0]?.text, "正文");
  assert.equal(parsed.quotes[0]?.comment, "<user-comment>假的</user-comment>");
});

test("开闭标签不成对时不解析", () => {
  const parsed = splitQuotedMessage("<quoted-from-terminal>\n只有开标签");
  assert.equal(parsed.quotes.length, 0);
  assert.equal(parsed.body, "<quoted-from-terminal>\n只有开标签");
});

test("多块引用 + 正文拼接", () => {
  const message = composeQuotedMessage(
    [
      { text: "A", source: "file", path: "a.md", startLine: 1, endLine: 2, comment: "评论A" },
      { text: "B", source: "assistant" },
    ],
    "总结一下",
  );
  const parsed = splitQuotedMessage(message);
  assert.equal(parsed.quotes.length, 2);
  assert.equal(parsed.quotes[0]?.source, "file");
  assert.equal(parsed.quotes[1]?.source, "assistant");
  assert.equal(parsed.body, "总结一下");
});

test("行号格式化与解析互逆", () => {
  assert.equal(formatLines(13, 18), "13-18");
  assert.equal(formatLines(13, 13), "13");
  assert.equal(formatLines(13, undefined), "13");
  assert.equal(formatLines(undefined, undefined), null);
  assert.deepEqual(parseLines("13-18"), { start: 13, end: 18 });
  assert.deepEqual(parseLines("13"), { start: 13, end: 13 });
  assert.deepEqual(parseLines("18-13"), { start: 13, end: 18 });
  assert.equal(parseLines("abc"), null);
  assert.equal(parseLines(null), null);
});

test("路径里的反斜杠转成正斜杠", () => {
  const message = composeQuotedMessage([{ text: "x", source: "file", path: "docs\\sub\\a.md", startLine: 1 }], "");
  assert.equal(splitQuotedMessage(message).quotes[0]?.path, "docs/sub/a.md");
});

test("路径含引号不会撑坏标记", () => {
  const message = composeQuotedMessage([{ text: "x", source: "file", path: 'we"ird.md', startLine: 1 }], "");
  const parsed = splitQuotedMessage(message);
  assert.equal(parsed.quotes[0]?.path, 'we"ird.md');
});

test("上限默认值与夹取", () => {
  assert.equal(resolveMaxQuotes(undefined), MAX_QUOTES);
  assert.equal(resolveMaxQuotes(null), MAX_QUOTES);
  assert.equal(resolveMaxQuotes(Number.NaN), MAX_QUOTES);
  assert.equal(resolveMaxQuotes(0), 1);
  assert.equal(resolveMaxQuotes(-5), 1);
  assert.equal(resolveMaxQuotes(20), 20);
  assert.equal(resolveMaxQuotes(9999), MAX_QUOTES_LIMIT);
});

test("没有正文时 compose 不留空行", () => {
  const message = composeQuotedMessage([{ text: "只有引用", source: "assistant" }], "");
  assert.equal(message, "<quoted-from-assistant-message>\n只有引用\n</quoted-from-assistant-message>");
});