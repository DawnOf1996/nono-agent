import { FileCode2, MessageSquare, Terminal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { formatLines, quotePreview, type QuoteSource } from "@/lib/quote";

/** 出处徽标：终端和文件需要标，模型回复里选的本来就是上下文的一部分。 */
function SourceBadge({ source, path }: { source: QuoteSource; path?: string | null }) {
  if (source === "terminal") {
    return (
      <span className="flex shrink-0 items-center gap-0.5 rounded-sm bg-hover px-1 py-0.5 text-[10px] text-muted" title="从终端选中的内容">
        <Terminal className="size-2.5" />
        终端
      </span>
    );
  }
  if (source === "file") {
    return (
      <span className="flex min-w-0 shrink items-center gap-0.5 rounded-sm bg-hover px-1 py-0.5 text-[10px] text-muted" title={path ?? undefined}>
        <FileCode2 className="size-2.5 shrink-0" />
        <span className="truncate">{path || "文件"}</span>
      </span>
    );
  }
  return null;
}

/** file 引用的行号范围，缺行号就不显示。 */
function LinesBadge({ startLine, endLine }: { startLine?: number | null; endLine?: number | null }) {
  const lines = formatLines(startLine ?? undefined, endLine ?? undefined);
  if (!lines) return null;
  return <span className="shrink-0 rounded-sm bg-hover px-1 py-0.5 text-[10px] text-muted tabular-nums">{lines}</span>;
}

/** 评论摘要，放在 chip 尾部，有评论才出现。 */
function CommentDot({ comment }: { comment?: string | null }) {
  if (!comment) return null;
  return (
    <span className="flex shrink-0 items-center gap-0.5 text-[10px] text-accent" title={comment}>
      <MessageSquare className="size-2.5" />
    </span>
  );
}

type QuoteView = {
  text: string;
  source: QuoteSource;
  path?: string | null;
  startLine?: number | null;
  endLine?: number | null;
  comment?: string | null;
};

/** 悬停弹层里的全文：原文 + 评论。 */
function QuoteDetail({ quote }: { quote: QuoteView }) {
  return (
    <div className="max-h-40 overflow-auto rounded-xl border border-line bg-bg px-3 py-2 text-[13px] leading-5 break-words whitespace-pre-wrap text-ink shadow-[0_12px_32px_var(--shadow-color)]">
      {quote.text ? <div>{quote.text}</div> : null}
      {quote.comment ? (
        <div className={quote.text ? "mt-2 border-t border-line pt-2" : ""}>
          <div className="mb-1 flex items-center gap-1 text-[11px] text-muted">
            <MessageSquare className="size-3" />
            评论
          </div>
          {quote.comment}
        </div>
      ) : null}
    </div>
  );
}

/** 已发送消息里的引用：默认缩略，悬停看全文。 */
export function QuoteCard({ text, source = "assistant", path, startLine, endLine, comment }: QuoteView) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef(0);
  const [open, setOpen] = useState(false);
  const [box, setBox] = useState({ top: 0, left: 0, above: true });

  function place() {
    const node = anchorRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    const width = 288;
    const above = rect.top > 176;
    const left = Math.min(Math.max(rect.right, width + 8), window.innerWidth - 8);
    setBox({ top: above ? rect.top - 4 : rect.bottom + 4, left, above });
  }

  function show() {
    window.clearTimeout(hideTimer.current);
    place();
    setOpen(true);
  }

  function hide() {
    hideTimer.current = window.setTimeout(() => setOpen(false), 120);
  }

  useEffect(() => {
    return () => window.clearTimeout(hideTimer.current);
  }, []);

  useEffect(() => {
    if (!open) return;
    function onScroll(event: Event) {
      if (popupRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    }
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open]);

  const quote: QuoteView = { text, source, path, startLine, endLine, comment };

  return (
    <div ref={anchorRef} className="max-w-full" onMouseEnter={show} onMouseLeave={hide}>
      <div className="inline-flex max-w-[220px] items-center gap-1 rounded-lg border border-line bg-sidebar px-2 py-1 text-[12px] text-muted">
        <SourceBadge source={source} path={path} />
        <LinesBadge startLine={startLine} endLine={endLine} />
        <span className="truncate">{quotePreview(text || comment || "")}</span>
        <CommentDot comment={comment} />
      </div>
      {open
        ? createPortal(
            <div
              ref={popupRef}
              className="fixed z-40 w-72"
              style={{
                top: box.top,
                left: box.left,
                transform: box.above ? "translate(-100%, -100%)" : "translate(-100%, 0)",
              }}
              onMouseEnter={show}
              onMouseLeave={hide}
            >
              <QuoteDetail quote={quote} />
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

/** 输入框里的引用：短标签，悬停看全文，可移除。 */
export function QuoteChip({
  text,
  source = "assistant",
  path,
  startLine,
  endLine,
  comment,
  onRemove,
}: QuoteView & { onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const quote: QuoteView = { text, source, path, startLine, endLine, comment };

  return (
    <div
      className="relative max-w-full"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {open ? (
        <div className="absolute bottom-full left-0 z-30 w-72 pb-1">
          <QuoteDetail quote={quote} />
        </div>
      ) : null}
      <div className="inline-flex max-w-[220px] items-center gap-0.5 rounded-lg border border-line bg-sidebar py-1 pr-2 pl-1 text-[12px] text-muted">
        <button
          type="button"
          className="flex size-4 shrink-0 items-center justify-center rounded text-faint hover:bg-hover hover:text-ink"
          title="移除引用"
          aria-label="移除引用"
          onClick={onRemove}
        >
          <X className="size-3" />
        </button>
        <SourceBadge source={source} path={path} />
        <LinesBadge startLine={startLine} endLine={endLine} />
        <span className="truncate">{quotePreview(text || comment || "")}</span>
        <CommentDot comment={comment} />
      </div>
    </div>
  );
}