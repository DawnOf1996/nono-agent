import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addCustom,
  buildAnswers,
  canSubmit,
  emptyDraft,
  questionKey,
  removeCustom,
  toggleOption,
  type AskDraft,
  type AskQuestion,
} from "@/features/permissions/ask-answer";
import { CARD, SHELL } from "@/features/permissions/layout";

/**
 * AskUserQuestion 的回答卡片。
 *
 * 两条路都得在：点模型给的选项，或者自己写一句。选项点单选即刻提交，
 * 多选则先勾选再提交；输入框始终可用 —— 用户想说的不在选项里是常态，
 * 不该逼他在一个不合适的标签里凑合。
 */
export function AskUserCard({
  question,
  onAnswer,
  onSkip,
}: {
  question: AskQuestion;
  onAnswer: (answers: Record<string, unknown>) => void;
  onSkip: () => void;
}) {
  const multi = Boolean(question.multi_select);
  const options = question.options ?? [];
  const [draft, setDraft] = useState<AskDraft>(emptyDraft);
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // 换题时父层用 key 重挂载，这里天然是干净的草稿；顺手把光标放进输入框。
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function submit(next: AskDraft) {
    const answers = buildAnswers(question, next);
    if (Object.keys(answers).length) onAnswer(answers);
    else onSkip();
  }

  /**
   * 单选：写了就是答案，直接发。
   * 多选：这里只是把这一条**加进**草稿（和勾选的结果一起），等用户点提交 ——
   * 否则用户没法"勾两项再补一句说明"。
   */
  function commitText() {
    const next = addCustom(draft, text);
    if (next === draft) return;
    setText("");
    if (multi) {
      setDraft(next);
      return;
    }
    submit(next);
  }

  const prompt = questionKey(question) || "需要你决定一下";

  return (
    <div className={SHELL}>
      <div className={CARD}>
        <div className="text-xs text-faint">
          提问{question.header ? ` · ${question.header}` : ""}
        </div>
        <p className="mt-2 text-sm leading-6">{prompt}</p>

        {options.length > 0 ? (
          <div className="mt-4 flex flex-col gap-1.5">
            {options.map((option) => {
              const picked = draft.selected.includes(option.label);
              return (
                <button
                  key={option.label}
                  type="button"
                  aria-pressed={picked}
                  onClick={() => {
                    const next = toggleOption(draft, option.label, multi);
                    setDraft(next);
                    // 单选没有"再想想"这一步，点中即答；要改写就用下面的输入框。
                    if (!multi) submit(next);
                  }}
                  className={
                    picked
                      ? "rounded-xl border border-line-strong bg-hover px-3 py-2 text-left text-sm"
                      : "rounded-xl border border-line px-3 py-2 text-left text-sm hover:bg-hover"
                  }
                >
                  <span className="block">
                    {multi ? (
                      <span className="mr-1.5 text-muted">{picked ? "☑" : "☐"}</span>
                    ) : null}
                    {option.label}
                  </span>
                  {option.description ? (
                    <span className="mt-0.5 block text-xs text-faint">{option.description}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : null}

        {draft.custom.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {draft.custom.map((item) => (
              <span
                key={item}
                className="inline-flex items-center gap-1 rounded-full bg-hover px-2.5 py-1 text-xs"
              >
                {item}
                <button
                  type="button"
                  aria-label={`移除 ${item}`}
                  onClick={() => setDraft(removeCustom(draft, item))}
                  className="text-faint hover:text-ink"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}

        <div className="mt-4 flex items-center gap-2">
          <Input
            ref={inputRef}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              commitText();
            }}
            placeholder={multi ? "再补一条，或直接写你的答案" : "或者自己写一个答案"}
            aria-label="自定义答案"
          />
          <Button onClick={commitText} disabled={!text.trim()}>
            {multi ? "添加" : "发送"}
          </Button>
        </div>

        <div className="mt-3 flex items-center gap-2">
          <span className="text-xs text-faint">
            选项之外也可以直接输入，模型会收到你写的原文
          </span>
          {multi ? (
            <Button
              size="sm"
              className="ml-auto"
              disabled={!canSubmit(draft)}
              onClick={() => submit(draft)}
            >
              提交
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={onSkip}>
            跳过
          </Button>
        </div>
      </div>
    </div>
  );
}