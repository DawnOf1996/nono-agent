import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { quotePreview } from "@/lib/quote";

/** 已发送消息里的引用：默认缩略，悬停看全文。 */
export function QuoteCard({ text }: { text: string }) {
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

  return (
    <div ref={anchorRef} className="max-w-full" onMouseEnter={show} onMouseLeave={hide}>
      <div className="inline-flex max-w-[220px] items-center rounded-lg border border-line bg-sidebar px-2 py-1 text-[12px] text-muted">
        <span className="truncate">{quotePreview(text)}</span>
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
              <div className="max-h-40 overflow-auto rounded-xl border border-line bg-bg px-3 py-2 text-[13px] leading-5 break-words whitespace-pre-wrap text-ink shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
                {text}
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

/** 输入框里的引用：短标签，悬停看全文，可移除。 */
export function QuoteChip({ text, onRemove }: { text: string; onRemove: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className="relative max-w-full"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {open ? (
        <div className="absolute bottom-full left-0 z-30 w-72 pb-1">
          <div className="max-h-40 overflow-auto rounded-xl border border-line bg-bg px-3 py-2 text-[13px] leading-5 break-words whitespace-pre-wrap text-ink shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
            {text}
          </div>
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
        <span className="truncate">{quotePreview(text)}</span>
      </div>
    </div>
  );
}
