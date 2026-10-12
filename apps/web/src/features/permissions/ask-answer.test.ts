/**
 * AskUser 答案组装的测试。
 *
 * 这里是这次优化的核心约定：选项和**用户自己写的**答案合成同一份回灌数据，
 * 一条都不能被"不在选项里"过滤掉。CLI 的 parse_choice 是同一套语义，
 * 改一边记得改另一边。
 *
 *   node --experimental-strip-types --test src/features/permissions/ask-answer.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addCustom,
  buildAnswers,
  canSubmit,
  collectAnswers,
  emptyDraft,
  questionKey,
  removeCustom,
  toggleOption,
  type AskQuestion,
} from "./ask-answer.ts";

const SINGLE: AskQuestion = {
  question: "改哪些文件？",
  header: "范围",
  options: [{ label: "全部" }, { label: "只改文档" }],
};

const MULTI: AskQuestion = {
  question: "要加哪些测试？",
  multi_select: true,
  options: [{ label: "单测" }, { label: "集成" }],
};

test("空草稿答不了", () => {
  assert.equal(canSubmit(emptyDraft()), false);
  assert.deepEqual(buildAnswers(SINGLE, emptyDraft()), {});
});

test("单选：点中哪项就答哪项", () => {
  const draft = toggleOption(emptyDraft(), "只改文档", false);
  assert.deepEqual(buildAnswers(SINGLE, draft), { "改哪些文件？": "只改文档" });
});

test("单选：后点的替换先点的，不会攒成两项", () => {
  let draft = toggleOption(emptyDraft(), "全部", false);
  draft = toggleOption(draft, "只改文档", false);
  assert.deepEqual(draft.selected, ["只改文档"]);
  assert.equal(buildAnswers(SINGLE, draft)["改哪些文件？"], "只改文档");
});

test("单选：完全绕开选项的原文能回灌", () => {
  const draft = addCustom(emptyDraft(), "  先只改 README，别动代码  ");
  assert.deepEqual(buildAnswers(SINGLE, draft), {
    "改哪些文件？": "先只改 README，别动代码",
  });
});

test("多选：勾选与手写合并成一份列表", () => {
  let draft = toggleOption(emptyDraft(), "单测", true);
  draft = toggleOption(draft, "集成", true);
  draft = addCustom(draft, "再加一条冒烟");
  assert.deepEqual(buildAnswers(MULTI, draft), {
    "要加哪些测试？": ["单测", "集成", "再加一条冒烟"],
  });
});

test("多选：再点一次取消勾选", () => {
  const picked = toggleOption(emptyDraft(), "单测", true);
  assert.deepEqual(toggleOption(picked, "单测", true).selected, []);
});

test("空输入不算一条答案", () => {
  const draft = addCustom(emptyDraft(), "   ");
  assert.deepEqual(draft.custom, []);
  assert.equal(canSubmit(draft), false);
});

test("手写内容与选项撞车时不重复回两遍", () => {
  let draft = toggleOption(emptyDraft(), "单测", true);
  draft = addCustom(draft, "单测");
  assert.deepEqual(collectAnswers(draft), ["单测"]);
});

test("手写内容可以撤掉", () => {
  const draft = removeCustom(addCustom(emptyDraft(), "再想想"), "再想想");
  assert.deepEqual(draft.custom, []);
});

test("题干缺了退回用 header 当键", () => {
  assert.equal(questionKey({ header: "  确认  " }), "确认");
  assert.equal(questionKey({}), "");
});

test("连题干都没有就不硬造一个键", () => {
  assert.deepEqual(buildAnswers({}, addCustom(emptyDraft(), "都行")), {});
});