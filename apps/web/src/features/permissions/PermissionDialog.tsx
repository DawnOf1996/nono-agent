import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chat";

export function PermissionDialog() {
  const { permissions, ask, resolvePermission, resolveAsk } = useChatStore();
  const pending = permissions[0];

  if (ask?.questions?.length) {
    const question = ask.questions[0];
    const prompt = question.question || question.header || "需要你选一项";
    return (
      <div className="fixed inset-0 z-20 flex items-end justify-center bg-black/25 p-6">
        <div className="w-full max-w-lg rounded-2xl border border-line bg-bg p-5 shadow-xl">
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
    <div className="fixed inset-0 z-20 flex items-end justify-center bg-black/25 p-6">
      <div className="w-full max-w-lg rounded-2xl border border-line bg-bg p-5 shadow-xl">
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
