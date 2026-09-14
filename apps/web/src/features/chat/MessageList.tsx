import { Copy } from "lucide-react";

import { ReasoningRow } from "@/features/chat/ReasoningRow";
import type { ChatMessage } from "@/store/chat";
import { useChatStore } from "@/store/chat";

function renderInline(text: string): Array<string | { code: string }> {
  const parts: Array<string | { code: string }> = [];
  const re = /`([^`]+)`/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    parts.push({ code: match[1] });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function MarkdownText({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="prose-md text-[15px] leading-7 text-ink">
      {blocks.map((block, index) => {
        const lines = block.split("\n");
        if (lines.every((line) => /^\s*\d+\.\s+/.test(line))) {
          return (
            <ol key={index} className="list-decimal pl-5">
              {lines.map((line) => (
                <li key={line}>{renderInline(line.replace(/^\s*\d+\.\s+/, "")).map(inlineNode)}</li>
              ))}
            </ol>
          );
        }
        return (
          <p key={index}>
            {lines.map((line, lineIndex) => (
              <span key={lineIndex}>
                {lineIndex > 0 ? <br /> : null}
                {renderInline(line).map(inlineNode)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function inlineNode(part: string | { code: string }, index: number) {
  if (typeof part === "string") return <span key={index}>{part}</span>;
  return <code key={index}>{part.code}</code>;
}

function ToolCard({ name }: { name: string }) {
  return (
    <div className="mb-3 overflow-hidden rounded-xl bg-card">
      <div className="flex items-center justify-between px-3 py-2 text-[12px] text-muted">
        <span className="font-mono">{name}</span>
        <button
          type="button"
          className="text-faint hover:text-ink"
          onClick={() => void navigator.clipboard.writeText(name)}
        >
          <Copy className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

function MessageBlock({ message }: { message: ChatMessage }) {
  if (message.role === "system") {
    return (
      <div className="rounded-2xl border border-line bg-sidebar px-4 py-3 font-mono text-[13px] leading-6 whitespace-pre-wrap text-muted">
        {message.text}
      </div>
    );
  }
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[min(42rem,92%)] rounded-2xl bg-card px-4 py-2.5 text-[15px] leading-7">
          {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[min(48rem,100%)]">
      {message.thinking || message.thinkingRunning ? (
        <ReasoningRow text={message.thinking ?? ""} running={Boolean(message.thinkingRunning)} />
      ) : null}
      {message.tools?.map((tool) => (
        <ToolCard key={tool} name={tool} />
      ))}
      {message.text ? <MarkdownText text={message.text} /> : null}
      {message.streaming && !message.text && !message.thinking ? (
        <p className="text-sm text-faint">正在写入…</p>
      ) : null}
      {message.text && !message.streaming ? (
        <div className="mt-2">
          <button
            type="button"
            className="text-faint hover:text-ink"
            title="复制"
            onClick={() => void navigator.clipboard.writeText(message.text)}
          >
            <Copy className="size-3.5" />
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function MessageList() {
  const messages = useChatStore((state) => state.messages);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-8">
        <div className="max-w-md text-center">
          <h1 className="text-2xl font-medium tracking-tight">把任务交给本地引擎</h1>
          <p className="mt-3 text-sm leading-6 text-muted">
            先选工作空间，再选模型。工具读写都限制在这个目录里。
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
