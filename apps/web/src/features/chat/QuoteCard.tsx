import { X } from "lucide-react";
import { useState } from "react";

import { quotePreview } from "@/lib/quote";

/** 已发送消息里的引用：只读小卡片。 */
export function QuoteCard({ text }: { text: string }) {
  return (
    <div className="max-w-full rounded-xl border border-line bg-bg px-3 py-2 text-left shadow-[0_4px_16px_rgba(15,17,21,0.05)]">
      <div className="max-h-32 overflow-auto text-[13px] leading-5 break-words whitespace-pre-wrap text-muted">{text}</div>
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
