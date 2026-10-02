import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chat";

// 非模态：只盖住对话区，且不吃点击。挂起等授权时仍能切到别的会话。
const SHELL =
  "pointer-events-none fixed inset-y-0 left-[260px] right-0 z-20 flex items-end justify-center p-6";
const CARD =
  "pointer-events-auto w-full max-w-lg rounded-2xl border border-line bg-bg p-5 shadow-[0_16px_48px_var(--shadow-color-strong)]";

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

  if (ask?.questions?.length) {
    const question = ask.questions[0];
    const prompt = question.question || question.header || "需要你选一项";
    return (
      <div className={SHELL}>
        <div className={CARD}>
          <div className="text-xs text-faint">提问</div>
          <p className="mt-2 text-sm leading-6">{prompt}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {(question.options ?? []).map((option) => (
              <Button key={option.label} size="sm" onClick={() => void resolveAsk({ [prompt]: option.label })}>
                {option.label}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={() => void resolveAsk({})}>
              跳过
            </Button>
          </div>
        </div>
      </div>
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
