import { Square } from "lucide-react";
import type { KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

export function Composer() {
  const { input, setInput, send, abort, streaming, workspace, model, setNewTaskOpen } =
    useChatStore();

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <div className="border-t border-line bg-panel px-6 py-4">
      <div className="mx-auto max-w-3xl">
        <div className="mb-2 flex flex-wrap gap-2 font-mono text-[10px] uppercase tracking-wider text-muted">
          <button type="button" className="hover:text-copper" onClick={() => setNewTaskOpen(true)}>
            目录 {workspace ? workspaceLabel(workspace) : "未选"}
          </button>
          <span className="text-line">/</span>
          <span>模型 {model || "未选"}</span>
        </div>
        <div className="flex items-end gap-3">
          <Textarea
            rows={3}
            value={input}
            placeholder="描述要做的事。Enter 发送，Shift+Enter 换行。"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
          />
          {streaming ? (
            <Button variant="danger" onClick={() => void abort()}>
              <Square className="size-3 fill-current" />
              中止
            </Button>
          ) : (
            <Button onClick={() => void send()} disabled={!input.trim()}>
              发送
            </Button>
          )}
        </div>
        <div className="mt-3 h-[2px] overflow-hidden bg-line">
          <div
            className={`filament h-full bg-copper ${streaming ? "filament-live w-2/3" : "w-8 opacity-40"}`}
          />
        </div>
      </div>
    </div>
  );
}
