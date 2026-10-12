import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { AskUserCard } from "@/features/permissions/AskUserCard";
import { CARD, SHELL } from "@/features/permissions/layout";
import { useChatStore } from "@/store/chat";

export function PermissionDialog() {
  const permissions = useChatStore((state) => state.permissions);
  const ask = useChatStore((state) => state.ask);
  const sessionId = useChatStore((state) => state.sessionId);
  const resolvePermission = useChatStore((state) => state.resolvePermission);
  const resolveAsk = useChatStore((state) => state.resolveAsk);
  // 只弹当前会话的授权；别的会话继续挂起，等切回去再确认。
  const pending = permissions.find(
    (item) => !item.session_id || item.session_id === sessionId,
  );

  // 一次只问一道：逐题推进，最后一题答完才把 answers 一起回灌。
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const questions = ask?.questions ?? [];
  const current = questions[index];

  // 新的一次提问（新的 ask 信号）从第一题重来；ask 置空后这次重置是空操作。
  useEffect(() => {
    setIndex(0);
    setAnswers({});
  }, [ask]);

  if (current) {
    return (
      <AskUserCard
        key={index}
        question={current}
        onAnswer={(next) => {
          const merged = { ...answers, ...next };
          if (index + 1 < questions.length) {
            setAnswers(merged);
            setIndex(index + 1);
          } else {
            void resolveAsk(merged);
          }
        }}
        onSkip={() => {
          if (index + 1 < questions.length) {
            setIndex(index + 1);
          } else {
            void resolveAsk(answers);
          }
        }}
      />
    );
  }

  if (!pending) return null;

  return (
    <div className={SHELL}>
      <div className={CARD}>
        <div className="text-xs text-faint">工具权限</div>
        <p className="mt-2 text-sm leading-6">
          允许执行 <span className="font-mono text-[13px]">{pending.tool_name}</span> 吗？
        </p>
        <pre className="mt-3 max-h-40 overflow-auto rounded-xl bg-card p-3 font-mono text-[11px] text-muted">
          {JSON.stringify(pending.input_params, null, 2)}
        </pre>
        <div className="mt-4 flex gap-2">
          <Button onClick={() => void resolvePermission(pending.request_id, true)}>允许</Button>
          <Button variant="danger" onClick={() => void resolvePermission(pending.request_id, false)}>
            拒绝
          </Button>
        </div>
      </div>
    </div>
  );
}
