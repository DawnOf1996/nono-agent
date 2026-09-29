import { Check, Copy, Reply } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

import { useChatStore } from "@/store/chat";

type Anchor = {
  text: string;
  top: number;
  left: number;
  above: boolean;
};

function assistantMessage(node: Node, root: HTMLElement): HTMLElement | null {
  const element = node instanceof Element ? node : node.parentElement;
  const host = element?.closest<HTMLElement>("[data-quote-source='assistant']");
  if (!host || !root.contains(host)) return null;
  return host;
}

function selectionAnchor(root: HTMLElement): Anchor | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const text = selection.toString().replace(/\u00a0/g, " ").trim();
  if (!text) return null;
  const range = selection.getRangeAt(0);
  const startHost = assistantMessage(range.startContainer, root);
  const endHost = assistantMessage(range.endContainer, root);
  if (!startHost || startHost !== endHost) return null;

  const rects = range.getClientRects();
  let rect = range.getBoundingClientRect();
  for (let index = rects.length - 1; index >= 0; index -= 1) {
    const item = rects[index];
    if (item.width > 0 || item.height > 0) {
      rect = item;
      break;
    }
  }
  if (rect.width === 0 && rect.height === 0) return null;

  const rootRect = root.getBoundingClientRect();
  if (rect.bottom < rootRect.top + 4 || rect.top > rootRect.bottom - 4) return null;

  const barWidth = 180;
  const barHeight = 40;
  const margin = 8;
  let left = rect.right;
  left = Math.max(rootRect.left + margin + barWidth, left);
  left = Math.min(window.innerWidth - margin, left);
  const above = rect.top - barHeight - 6 >= rootRect.top + margin;
  const top = above ? rect.top - 6 : rect.bottom + 6;
  return { text, top, left, above };
}

/** 在模型回复里松开鼠标选中文字后，浮出复制和「添加到对话」。 */
export function SelectionQuote({ containerRef }: { containerRef: RefObject<HTMLElement | null> }) {
  const addQuote = useChatStore((state) => state.addQuote);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [copied, setCopied] = useState(false);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;

    let selecting = false;
    let timer = 0;

    const place = () => {
      timer = 0;
      setAnchor(selectionAnchor(root));
    };
    const placeAfterRelease = () => {
      if (timer) return;
      timer = window.setTimeout(place, 0);
    };

    function onPointerDown(event: PointerEvent) {
      if (event.button !== 0) return;
      if (barRef.current?.contains(event.target as Node)) return;
      selecting = true;
      if (timer) {
        window.clearTimeout(timer);
        timer = 0;
      }
      setAnchor(null);
    }

    function onPointerUp(event: PointerEvent) {
      if (event.button !== 0 || !selecting) return;
      selecting = false;
      placeAfterRelease();
    }

    function onPointerCancel() {
      selecting = false;
    }

    const onSelectionChange = () => {
      if (selecting) return;
      setAnchor((current) => (current && !selectionAnchor(root) ? null : current));
    };

    const onScroll = () => {
      if (selecting) return;
      setAnchor((current) => (current ? selectionAnchor(root) : current));
    };

    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
    document.addEventListener("selectionchange", onSelectionChange);
    root.addEventListener("scroll", onScroll);
    window.addEventListener("resize", onScroll);
    return () => {
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerCancel);
      document.removeEventListener("selectionchange", onSelectionChange);
      root.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      setAnchor(null);
    };
  }, [containerRef]);

  useEffect(() => {
    setCopied(false);
  }, [anchor?.text]);

  useEffect(() => {
    if (!anchor) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      window.getSelection()?.removeAllRanges();
      setAnchor(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [anchor]);

  if (!anchor) return null;

  return createPortal(
    <div
      ref={barRef}
      role="toolbar"
      aria-label="选中文字"
      className="fixed z-40 flex items-center gap-0.5 rounded-lg border border-line bg-bg p-1 shadow-[0_8px_24px_rgba(15,17,21,0.12)]"
      style={{
        top: anchor.top,
        left: anchor.left,
        transform: anchor.above ? "translate(-100%, -100%)" : "translate(-100%, 0)",
      }}
      onPointerDown={(event) => event.preventDefault()}
    >
      <button
        type="button"
        className="flex items-center gap-1 rounded-md px-2 py-1 text-[13px] text-ink hover:bg-hover"
        onClick={() => {
          void navigator.clipboard.writeText(anchor.text).then(
            () => setCopied(true),
            () => setCopied(false),
          );
        }}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        {copied ? "已复制" : "复制"}
      </button>
      <button
        type="button"
        className="flex items-center gap-1 rounded-md bg-active px-2 py-1 text-[13px] text-ink hover:bg-hover"
        onClick={() => {
          addQuote(anchor.text);
          window.getSelection()?.removeAllRanges();
          setAnchor(null);
        }}
      >
        <Reply className="size-3.5" />
        添加到对话
      </button>
    </div>,
    document.body,
  );
}
