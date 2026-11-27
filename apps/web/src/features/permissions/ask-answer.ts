/**
 * AskUser 问卷的答案组装 —— 纯函数，不碰 React。
 *
 * 背景：模型给的 options 只是它的**猜测**，用户真正想说的常常不在里面
 * （要改个文件名、要一个它没列的场景、要拒绝但不选「拒绝」这个标签）。
 * 所以每道题除了选项，还留了一条自由输入的口子：选项点了就回，
 * 想自己写就直接写，写的内容和选中的选项一并回给模型。
 *
 * 组件（AskUserCard.tsx）只管交互，合并规则都在这里，方便直接测。
 */

import type { AskUserSignal } from "@nono/protocol";

/** 直接派生自协议里的 ask_user 事件 —— 结构只定义一处，UI 不再各写各的。 */
export type AskQuestion = NonNullable<AskUserSignal["questions"]>[number];

/** 用户对一道题的作答草稿：勾了哪些选项 + 自己写了哪几行。 */
export type AskDraft = {
  /** 已选选项的 label，保持点击顺序；单选时最多一个。 */
  selected: string[];
  /** 用户手写的答案，一行一条。 */
  custom: string[];
};

export function emptyDraft(): AskDraft {
  return { selected: [], custom: [] };
}

/** 答案回灌时用的键 —— 与 CLI 保持一致，都用题干。 */
export function questionKey(question: AskQuestion): string {
  return (question.question || question.header || "").trim();
}

/** 单选时点一下就切换到该项；多选时自由增减。 */
export function toggleOption(
  draft: AskDraft,
  label: string,
  multi: boolean,
): AskDraft {
  if (multi) {
    return {
      ...draft,
      selected: draft.selected.includes(label)
        ? draft.selected.filter((item) => item !== label)
        : [...draft.selected, label],
    };
  }
  if (draft.selected[0] === label) return draft;
  return { ...draft, selected: [label] };
}

/** 追加一条手写答案；空串不算，避免把「用户没填」当成一条答案。 */
export function addCustom(draft: AskDraft, text: string): AskDraft {
  const value = text.trim();
  if (!value || draft.custom.includes(value)) return draft;
  return { ...draft, custom: [...draft.custom, value] };
}

export function removeCustom(draft: AskDraft, value: string): AskDraft {
  return { ...draft, custom: draft.custom.filter((item) => item !== value) };
}

/** 选中的选项 + 手写的答案，去重后合成一份有序清单。 */
export function collectAnswers(draft: AskDraft): string[] {
  const merged: string[] = [];
  for (const item of [...draft.selected, ...draft.custom]) {
    if (item && !merged.includes(item)) merged.push(item);
  }
  return merged;
}

export function canSubmit(draft: AskDraft): boolean {
  return collectAnswers(draft).length > 0;
}

/**
 * 组装成 `POST /session/<id>/answer` 的 `answers`。
 *
 * 单选给字符串、多选给数组，和 CLI 的 `parse_choice` 保持同一种形状。
 * 一条都没答（跳过）返回空对象 —— 与改动前一致，服务端照样放行。
 */
export function buildAnswers(
  question: AskQuestion,
  draft: AskDraft,
): Record<string, unknown> {
  const entries = collectAnswers(draft);
  if (!entries.length) return {};
  const key = questionKey(question);
  if (!key) return {};
  return { [key]: question.multi_select ? entries : entries[0] };
}