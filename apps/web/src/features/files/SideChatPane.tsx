import { ArrowUp, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchSessionEvents, postChat, promoteSideSession } from "@/api/client";
import { extractAssistantText, extractTextDelta, messagesFromEvents, parseSse } from "@/api/sse";
import { MarkdownText } from "@/components/MarkdownText";
import { usePanelStore } from "@/features/files/panel-store";
import { useChatStore } from "@/store/chat";

type Line = { id: string; role: "user" | "assistant"; text: string };

export function SideChatPane({
  tabId,
  productId,
  engineId,
}: {
  tabId: string;
  productId: string;
  engineId: string;
}) {
  const model = useChatStore((state) => state.model);
  const permissionMode = useChatStore((state) => state.permissionMode);
  const refresh = useChatStore((state) => state.refresh);
  const openSession = useChatStore((state) => state.openSession);
  const close = usePanelStore((state) => state.close);
  const [lines, setLines] = useState<Line[]>([]);
  const [draft, setDraft] = useState("");
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [promoting, setPromoting] = useState(false);

  useEffect(() => {
    let live = true;
    void fetchSessionEvents(engineId).then((events) => {
      if (!live) return;
      setLines(messagesFromEvents(events).map((item) => ({ id: item.id, role: item.role, text: item.text })));
    });
    return () => {
      live = false;
    };
  }, [engineId]);

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setError(null);
    setStreaming(true);
    setDraft("");
    setLines((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", text },
      { id: "draft", role: "assistant", text: "" },
    ]);
    try {
      const response = await postChat(engineId, text, model || undefined, permissionMode);
      if (!response.ok) throw new Error(await response.text());
      let next = "";
      for await (const frame of parseSse(response)) {
        if (frame.event === "done") break;
        if (frame.event === "error") {
          const payload = frame.data ? (JSON.parse(frame.data) as { error?: string }) : {};
          throw new Error(payload.error || "这条侧边对话失败了");
        }
        if (!frame.data) continue;
        const payload = JSON.parse(frame.data) as unknown;
        if (frame.event === "stream_event") {
          next += extractTextDelta(payload);
          setDraft(next);
        } else if (frame.event === "assistant") {
          const full = extractAssistantText(payload);
          if (full) {
            next = full;
            setDraft(full);
          }
        }
      }
      const events = await fetchSessionEvents(engineId);
      setLines(messagesFromEvents(events).map((item) => ({ id: item.id, role: item.role, text: item.text })));
      setDraft("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发送失败");
      setLines((current) => current.filter((item) => item.id !== "draft"));
    } finally {
      setStreaming(false);
    }
  }

  async function promote() {
    setPromoting(true);
    setError(null);
    try {
      const row = await promoteSideSession(productId);
      close(tabId);
      await refresh();
      await openSession(row.engine_session_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法保存为新会话");
      setPromoting(false);
    }
  }

  const shown = lines.map((line) => (line.id === "draft" ? { ...line, text: draft || "…" } : line));

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-line px-2">
        <p className="min-w-0 flex-1 truncate text-[12px] text-muted">继承主会话上下文，不会写回主会话</p>
        <button
          type="button"
          className="h-7 shrink-0 rounded-md px-2 text-[12px] text-ink hover:bg-hover disabled:opacity-40"
          disabled={promoting || streaming}
          onClick={() => void promote()}
        >
          {promoting ? "正在保存" : "保存为新会话"}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        {shown.length === 0 ? (
          <p className="text-[13px] leading-6 text-muted">这条侧边对话已经带上主会话到目前为止的上下文。直接追问即可。</p>
        ) : (
          <div className="flex flex-col gap-3">
            {shown.map((line) => (
              <div key={line.id}>
                <p className="mb-1 text-[11px] text-faint">{line.role === "user" ? "你" : "侧边对话"}</p>
                <MarkdownText text={line.text} streaming={line.id === "draft"} />
              </div>
            ))}
          </div>
        )}
      </div>
      {error ? <p className="px-3 pb-1 text-[12px] text-danger">{error}</p> : null}
      <form
        className="flex items-end gap-2 border-t border-line px-2 py-2"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          value={input}
          rows={2}
          placeholder="在这条侧边对话里追问"
          className="min-h-10 min-w-0 flex-1 resize-none bg-transparent text-[13px] leading-5 outline-none placeholder:text-faint"
          disabled={streaming}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="submit"
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-white disabled:opacity-30"
          disabled={streaming || !input.trim()}
          aria-label="发送"
        >
          {streaming ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowUp className="size-4" />}
        </button>
      </form>
    </div>
  );
}
