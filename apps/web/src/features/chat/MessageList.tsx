import { ChevronDown, Copy } from "lucide-react";
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ImageLightbox } from "@/components/ImageLightbox";
import { MarkdownText } from "@/components/MarkdownText";
import { ReasoningRow } from "@/features/chat/ReasoningRow";
import { TurnRail, groupChatTurns } from "@/features/chat/TurnRail";
import type { ChatImage, ChatMessage } from "@/store/chat";
import { useChatStore } from "@/store/chat";

function MessageImages({ images, messageId }: { images: ChatImage[]; messageId: string }) {
  const [preview, setPreview] = useState<string | null>(null);

  return (
    <>
      <div className="flex flex-wrap justify-end gap-2">
        {images.map((image, index) => (
          <button
            key={`${messageId}-img-${index}`}
            type="button"
            className="block shrink-0 cursor-zoom-in overflow-hidden rounded-2xl border border-line bg-bg p-1.5 shadow-[0_8px_24px_rgba(15,17,21,0.06)]"
            title="查看大图"
            onClick={() => setPreview(image.src)}
          >
            <img
              src={image.src}
              alt=""
              className="max-h-56 max-w-80 rounded-xl object-contain"
            />
          </button>
        ))}
      </div>
      <ImageLightbox src={preview} onClose={() => setPreview(null)} />
    </>
  );
}

const NEAR_BOTTOM_PX = 96;

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
        <div className="flex max-w-[min(42rem,92%)] flex-col items-end gap-2">
          {message.images?.length ? (
            <MessageImages images={message.images} messageId={message.id} />
          ) : null}
          {message.text.trim() ? (
            <div className="rounded-2xl bg-card px-4 py-2.5">
              <MarkdownText text={message.text} />
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  const liveInThinking = Boolean(message.thinkingRunning && message.steps?.length);
  const answer = liveInThinking ? "" : message.text;
  const showThinking = Boolean(
    message.thinking ||
      message.thinkingRunning ||
      message.steps?.length ||
      liveInThinking,
  );

  return (
    <div className="max-w-[min(48rem,100%)]">
      {showThinking ? (
        <ReasoningRow
          thinking={message.thinking}
          steps={message.steps}
          liveText={liveInThinking ? message.text : undefined}
          running={Boolean(message.streaming)}
          durationMs={message.durationMs}
        />
      ) : null}
      {answer ? <MarkdownText text={answer} /> : null}
      {message.streaming && !answer && !showThinking ? (
        <p className="text-sm text-faint">正在写入…</p>
      ) : null}
      {answer && !message.streaming ? (
        <div className="mt-2">
          <button
            type="button"
            className="text-faint hover:text-ink"
            title="复制"
            onClick={() => void navigator.clipboard.writeText(answer)}
          >
            <Copy className="size-3.5" />
          </button>
        </div>
      ) : null}
    </div>
  );
}

function isNearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
}

export function MessageList() {
  const messages = useChatStore((state) => state.messages);
  const sessionId = useChatStore((state) => state.sessionId);
  const streaming = useChatStore((state) => state.streaming);
  const historyReveal = useChatStore((state) => state.historyReveal);
  const historyLoading = useChatStore((state) => state.historyLoading);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const sessionRef = useRef(sessionId);
  const heightRef = useRef(0);
  const countRef = useRef(0);
  const [showJump, setShowJump] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const turns = useMemo(() => groupChatTurns(messages), [messages]);
  const turnStartIds = useMemo(() => new Set(turns.map((item) => item.id)), [turns]);

  const syncActiveTurn = useCallback(() => {
    const root = scrollerRef.current;
    if (!root) return;
    const nodes = root.querySelectorAll<HTMLElement>("[data-turn-id]");
    if (!nodes.length) {
      setActiveTurnId(null);
      return;
    }
    const target = root.getBoundingClientRect().top + 72;
    let current = nodes[0].dataset.turnId ?? null;
    for (const node of nodes) {
      if (node.getBoundingClientRect().top <= target) current = node.dataset.turnId ?? current;
    }
    setActiveTurnId(current);
  }, []);

  const syncJump = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const near = isNearBottom(el);
    stickRef.current = near;
    setShowJump(!near && el.scrollHeight > el.clientHeight + 8);
    syncActiveTurn();
  }, [syncActiveTurn]);

  useLayoutEffect(() => {
    if (streaming) stickRef.current = true;
  }, [streaming]);

  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const sessionChanged = sessionRef.current !== sessionId;
    if (sessionChanged) stickRef.current = true;

    if (stickRef.current) {
      el.scrollTop = el.scrollHeight;
    } else {
      const delta = el.scrollHeight - heightRef.current;
      if (delta > 0 && messages.length > countRef.current) el.scrollTop += delta;
    }

    sessionRef.current = sessionId;
    heightRef.current = el.scrollHeight;
    countRef.current = messages.length;
    syncJump();
  }, [messages, sessionId, syncJump]);

  function jumpToTurn(id: string) {
    const root = scrollerRef.current;
    const node = root?.querySelector<HTMLElement>(`[data-turn-id="${CSS.escape(id)}"]`);
    if (!root || !node) return;
    stickRef.current = false;
    setActiveTurnId(id);
    const top = node.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 12;
    root.scrollTop = Math.max(0, top);
  }

  function jumpToBottom() {
    const el = scrollerRef.current;
    if (!el) return;
    stickRef.current = true;
    setShowJump(false);
    el.scrollTop = el.scrollHeight;
  }

  if (messages.length === 0) {
    if (historyLoading) {
      return <div className="min-h-0 flex-1" />;
    }
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-8">
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
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollerRef}
        className="absolute inset-0 overflow-auto px-8 py-6 pr-16"
        onScroll={syncJump}
      >
        <div
          className={`mx-auto flex max-w-3xl flex-col gap-8 ${historyReveal ? "chat-history-reveal" : ""}`}
        >
          {messages.map((message) => (
            <div
              key={message.id}
              className="chat-msg"
              data-turn-id={turnStartIds.has(message.id) ? message.id : undefined}
            >
              <MessageBlock message={message} />
            </div>
          ))}
        </div>
      </div>
      <TurnRail turns={turns} activeId={activeTurnId} onJump={jumpToTurn} />
      {showJump ? (
        <button
          type="button"
          className="absolute bottom-4 left-1/2 z-10 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-bg text-ink shadow-[0_8px_24px_rgba(15,17,21,0.12)] hover:bg-hover"
          title="回到底部"
          onClick={jumpToBottom}
        >
          <ChevronDown className="size-4" />
        </button>
      ) : null}
    </div>
  );
}
