import { useEffect, useRef, useState } from "react";

import type { DocumentPreviewProps } from "@/features/files/preview/types";

/** 图片预览：滚轮缩放，拖拽平移，双击回到适应窗口。 */
export function ImageBody({ content, name }: DocumentPreviewProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const [scale, setScale] = useState(1);
  const [origin, setOrigin] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    function onWheel(event: WheelEvent) {
      event.preventDefault();
      setScale((current) => {
        const next = event.deltaY < 0 ? current * 1.1 : current / 1.1;
        return Math.min(8, Math.max(0.2, next));
      });
    }
    frame.addEventListener("wheel", onWheel, { passive: false });
    return () => frame.removeEventListener("wheel", onWheel);
  }, []);

  if (content.kind !== "url") return null;

  function reset() {
    setScale(1);
    setOrigin({ x: 0, y: 0 });
  }

  return (
    <div ref={frameRef} className="relative h-full overflow-hidden bg-sidebar">
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-lg border border-line bg-bg/90 px-1 py-1 text-[12px]">
        <button type="button" className="size-6 rounded hover:bg-hover" aria-label="缩小" onClick={() => setScale((value) => Math.max(0.2, value / 1.2))}>
          −
        </button>
        <span className="w-10 text-center tabular-nums text-muted">{Math.round(scale * 100)}%</span>
        <button type="button" className="size-6 rounded hover:bg-hover" aria-label="放大" onClick={() => setScale((value) => Math.min(8, value * 1.2))}>
          +
        </button>
        <button type="button" className="h-6 rounded px-1.5 text-muted hover:bg-hover hover:text-ink" onClick={reset}>
          适应
        </button>
      </div>
      <div
        className="flex h-full cursor-grab items-center justify-center active:cursor-grabbing"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX, y: event.clientY, ox: origin.x, oy: origin.y };
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start) return;
          setOrigin({ x: start.ox + event.clientX - start.x, y: start.oy + event.clientY - start.y });
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onDoubleClick={reset}
      >
        <img
          src={content.url}
          alt={name}
          draggable={false}
          className="max-h-full max-w-full object-contain"
          style={{ transform: `translate(${origin.x}px, ${origin.y}px) scale(${scale})` }}
        />
      </div>
    </div>
  );
}
