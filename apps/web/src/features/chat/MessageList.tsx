import { Badge } from "@/components/ui/badge";
import type { ChatMessage } from "@/store/chat";
import { useChatStore } from "@/store/chat";

function MessageBlock({ message }: { message: ChatMessage }) {
  if (message.role === "system") {
    return (
      <div className="font-mono text-[11px] text-muted">{message.text}</div>
    );
  }

  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[min(42rem,92%)] border-l-2 border-copper/80 pl-3">
        <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-copper">你</div>
        <p className="mt-1 whitespace-pre-wrap text-[15px] leading-7">{message.text}</p>
      </div>
    );
  }

  return (
    <div className="max-w-[min(48rem,100%)]">
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-steel">
        助手
        {message.streaming ? <span className="text-copper">写入中</span> : null}
      </div>
      {message.tools?.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {message.tools.map((tool) => (
            <Badge key={tool} className="border-steel/40 text-steel">
              {tool}
            </Badge>
          ))}
        </div>
      ) : null}
      <p className="mt-2 whitespace-pre-wrap text-[15px] leading-7 text-paper/95">
        {message.text || (message.streaming ? "…" : "")}
      </p>
    </div>
  );
}

export function MessageList() {
  const messages = useChatStore((state) => state.messages);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-8">
        <div className="max-w-md">
          <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-copper">
            本地 sidecar
          </div>
          <h1 className="mt-3 text-3xl font-medium tracking-tight">把任务交给本地引擎</h1>
          <p className="mt-3 text-sm leading-6 text-muted">
            先选工作空间目录，再选模型。读写都限制在这个文件夹里，和 WorkBuddy 的任务流一样。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto px-8 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-8">
        {messages.map((message) => (
          <MessageBlock key={message.id} message={message} />
        ))}
      </div>
    </div>
  );
}
