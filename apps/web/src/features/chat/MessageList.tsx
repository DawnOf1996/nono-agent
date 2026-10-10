import type { TurnChanges } from "@nono/protocol";
import { ChevronDown, FileDown, GitFork } from "lucide-react";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { writeMarkdownFile } from "@/api/client";
import { ImageLightbox } from "@/components/ImageLightbox";
import { MarkdownText } from "@/components/MarkdownText";
import { CopyButton } from "@/features/chat/CopyButton";
import { QuoteCard } from "@/features/chat/QuoteCard";
import { ReasoningRow } from "@/features/chat/ReasoningRow";
import { SelectionQuote } from "@/features/chat/SelectionQuote";
import { TurnChangesCard, turnForMessage } from "@/features/chat/TurnChangesCard";
import { TurnRail, groupChatTurns } from "@/features/chat/TurnRail";
import { TurnUsageStat } from "@/features/chat/TurnUsageStat";
import { UserBubble } from "@/features/chat/UserBubble";
import { splitQuotedMessage } from "@/lib/quote";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { formatFullTime, formatMessageTime } from "@/lib/format";
import { suggestMarkdownFileName } from "@/lib/markdown-file";
import type { ChatImage, ChatMessage } from "@/store/chat";
import { useChatStore } from "@/store/chat";

/** 气泡旁的时刻。缺时间戳就不占位。 */
function MessageStamp({ at }: { at?: number }) {
  const label = formatMessageTime(at);
  if (!label) return null;
  return (
    <span className="text-[12px] tabular-nums text-faint" title={formatFullTime(at)}>
      {label}
    </span>
  );
}

/**
 * 用户消息复制出去的内容。
 *
 * 原文里夹着引用标记（<quoted-from-assistant-message>… / <quoted-from-terminal>…），
 * 粘到别处是乱码 —— 把引用原文摊平成普通段落，和正文一起给出去。
 */
function userCopyText(text: string): string {
  const { quotes, body } = splitQuotedMessage(text);
  if (!quotes.length) return body || text;
  return [...quotes.map((quote) => quote.text), body].filter(Boolean).join("\n\n");
}

function MessageImages({ images, messageId }: { images: ChatImage[]; messageId: string }) {
  const [preview, setPreview] = useState<string | null>(null);

  return (
    <>
      <div className="flex flex-wrap justify-end gap-2">
        {images.map((image, index) => (
          <button
            key={`${messageId}-img-${index}`}
            type="button"
            className="block shrink-0 cursor-zoom-in overflow-hidden rounded-2xl border border-line bg-bg p-1.5 shadow-[0_8px_24px_var(--shadow-color-soft)]"
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

/** memo：流式追加时只有最后一条消息在变，历史消息不该重新渲染。 */
const MessageBlock = memo(function MessageBlock({
  message,
  changes,
  onExport,
}: {
  message: ChatMessage;
  changes?: TurnChanges;
  onExport?: (text: string) => void;
}) {
  if (message.role === "system") {
    return (
      <div className="rounded-2xl border border-line bg-sidebar px-4 py-3 font-mono text-[13px] leading-6 whitespace-pre-wrap text-muted">
        {message.text}
      </div>
    );
  }
  if (message.role === "user") {
    const quoted = splitQuotedMessage(message.text);
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[min(42rem,92%)] flex-col items-end gap-2">
          {message.images?.length ? (
            <MessageImages images={message.images} messageId={message.id} />
          ) : null}
          {quoted.quotes.map((quote, index) => (
            <QuoteCard key={`${message.id}-quote-${index}`} text={quote.text} source={quote.source} />
          ))}
          {quoted.body ? <UserBubble text={quoted.body} /> : null}
          <div className="flex items-center gap-2 pr-1">
            <MessageStamp at={message.createdAt} />
            {message.text ? <CopyButton text={userCopyText(message.text)} /> : null}
          </div>
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
      {answer ? <MarkdownText text={answer} streaming={message.streaming} /> : null}
      {message.streaming && !answer && !showThinking ? (
        <p className="text-sm text-faint">正在写入…</p>
      ) : null}
      {changes ? <TurnChangesCard turn={changes} /> : null}
      {answer && !message.streaming ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <MessageStamp at={message.createdAt} />
          <CopyButton text={answer} />
          <button
            type="button"
            className="text-faint hover:text-ink"
            title="导出 Markdown"
            onClick={() => onExport?.(answer)}
          >
            <FileDown className="size-3.5" />
          </button>
          {typeof message.seq === "number" ? (
            <button
              type="button"
              className="text-faint hover:text-ink"
              title="从此处新建对话"
              onClick={() => {
                const seq = message.seq;
                if (typeof seq === "number") {
                  void useChatStore.getState().forkFromMessage(seq);
                }
              }}
            >
              <GitFork className="size-3.5" />
            </button>
          ) : null}
          {message.usage ? <TurnUsageStat usage={message.usage} /> : null}
        </div>
      ) : message.createdAt && !message.streaming ? (
        // 没有正文的轮次（只调了工具、或者中途失败）：时间照样要落在那儿。
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <MessageStamp at={message.createdAt} />
          {message.usage ? <TurnUsageStat usage={message.usage} /> : null}
        </div>
      ) : null}
    </div>
  );
});

function isNearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
}

export function MessageList() {
  const messages = useChatStore((state) => state.messages);
  const sessionId = useChatStore((state) => state.sessionId);
  const streaming = useChatStore((state) => state.streaming);
  const historyReveal = useChatStore((state) => state.historyReveal);
  const historyLoading = useChatStore((state) => state.historyLoading);
  const workspace = useChatStore((state) => state.workspace);
  const turnChanges = useChatStore((state) => (sessionId ? state.turnChanges[sessionId] : undefined));
  const [exportDraft, setExportDraft] = useState<{ text: string; name: string } | null>(null);
  const openExport = useCallback((text: string) => {
    setExportDraft({ text, name: suggestMarkdownFileName(text) });
  }, []);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  /** 最近一次由代码自己写入的 scrollTop，用来把随之而来的 scroll 事件认出来。 */
  const pinnedRef = useRef<number | null>(null);
  const sessionRef = useRef(sessionId);
  const heightRef = useRef(0);
  const countRef = useRef(0);
  const syncRafRef = useRef(0);
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

  // 读位置会强制同步布局，流式追加时每个分片都算一遍会把主线程压死 —— 按帧合并成一次。
  const scheduleSync = useCallback(() => {
    if (syncRafRef.current) return;
    syncRafRef.current = requestAnimationFrame(() => {
      syncRafRef.current = 0;
      const el = scrollerRef.current;
      if (!el) return;
      setShowJump(!stickRef.current && el.scrollHeight > el.clientHeight + 8);
      syncActiveTurn();
    });
  }, [syncActiveTurn]);

  useEffect(
    () => () => {
      if (syncRafRef.current) cancelAnimationFrame(syncRafRef.current);
    },
    [],
  );

  // 贴底要由代码自己写 scrollTop，浏览器会在下一帧补一个 scroll 事件回来；
  // 记下写进去的位置，回声事件就不会被当成「用户往上翻」。
  const pinBottom = useCallback((el: HTMLElement) => {
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    if (Math.abs(el.scrollTop - max) <= 1) return;
    pinnedRef.current = max;
    el.scrollTop = max;
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const pinned = pinnedRef.current;
    if (pinned !== null) {
      pinnedRef.current = null;
      // 位置没动过 —— 这是代码自己那次贴底的回声。内容在这中间长高（SVG/图表落定）不能算用户翻页。
      if (Math.abs(el.scrollTop - pinned) <= 1) {
        heightRef.current = el.scrollHeight;
        scheduleSync();
        return;
      }
    }
    // 是否贴底必须立刻记下来，否则下一个分片会把用户往回拽。
    stickRef.current = isNearBottom(el);
    scheduleSync();
  }, [scheduleSync]);

  useLayoutEffect(() => {
    if (streaming) stickRef.current = true;
  }, [streaming]);

  useLayoutEffect(() => {
    const el = scrollerRef.current;
    const sessionChanged = sessionRef.current !== sessionId;
    sessionRef.current = sessionId;
    if (!el) {
      // 列表为空时没有滚动容器，清掉基准：下次有内容就是一个全新视图。
      heightRef.current = 0;
      countRef.current = 0;
      return;
    }
    // 换会话、或列表刚从空变成有内容（重新载入历史）：都当新视图看，直接贴底。
    if (sessionChanged || countRef.current === 0) stickRef.current = true;

    if (stickRef.current) {
      pinBottom(el);
    } else {
      const delta = el.scrollHeight - heightRef.current;
      if (delta > 0 && messages.length > countRef.current) el.scrollTop += delta;
    }

    heightRef.current = el.scrollHeight;
    countRef.current = messages.length;
    scheduleSync();
  }, [messages, sessionId, scheduleSync, pinBottom]);

  // 图表和图片的高度要等绘制/解码之后才落定，消息列表本身不会因此重渲染。
  // 贴底时跟着内容尺寸把滚动补到真正的底部；往上翻阅时只更新高度基准，不把视口拽回去。
  const listReady = messages.length > 0;
  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!listReady || !content) return;
    let adjusting = false;
    const follow = () => {
      if (adjusting) return;
      const el = scrollerRef.current;
      if (!el) return;
      adjusting = true;
      if (stickRef.current) pinBottom(el);
      heightRef.current = el.scrollHeight;
      adjusting = false;
    };
    const observer = new ResizeObserver(follow);
    observer.observe(content);
    return () => observer.disconnect();
  }, [listReady]);

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
    pinBottom(el);
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
        onScroll={onScroll}
      >
        <div
          ref={contentRef}
          className={`mx-auto flex max-w-3xl flex-col gap-8 ${historyReveal ? "chat-history-reveal" : ""}`}
        >
          {messages.map((message) => (
            <div
              key={message.id}
              className="chat-msg"
              data-quote-source={message.role === "assistant" ? "assistant" : undefined}
              data-turn-id={turnStartIds.has(message.id) ? message.id : undefined}
            >
              <MessageBlock message={message} changes={turnForMessage(turnChanges, message)} onExport={openExport} />
            </div>
          ))}
        </div>
      </div>
      <SelectionQuote containerRef={scrollerRef} />
      <TurnRail turns={turns} activeId={activeTurnId} onJump={jumpToTurn} />
      <WorkspacePicker
        open={exportDraft != null}
        title="导出 Markdown"
        confirmLabel="保存"
        initialPath={workspace}
        fileName={exportDraft?.name ?? ""}
        onFileNameChange={(name) =>
          setExportDraft((current) => (current ? { ...current, name } : current))
        }
        onConfirm={async (directory) => {
          if (!exportDraft) return;
          await writeMarkdownFile(directory, exportDraft.name, exportDraft.text);
        }}
        onClose={() => setExportDraft(null)}
      />
      {showJump ? (
        <button
          type="button"
          className="absolute bottom-4 left-1/2 z-10 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-bg text-ink shadow-[0_8px_24px_var(--shadow-color)] hover:bg-hover"
          title="回到底部"
          onClick={jumpToBottom}
        >
          <ChevronDown className="size-4" />
        </button>
      ) : null}
    </div>
  );
}
