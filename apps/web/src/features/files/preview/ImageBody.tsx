import { useRef, useState } from "react";

import type { DocumentPreviewProps } from "@/features/files/preview/types";
import { usePanZoom } from "@/lib/use-pan-zoom";

const BUTTON_STEP = 1.2;

/** 图片预览：滚轮缩放，拖拽平移，双击回到适应窗口。 */
export function ImageBody({ content, name }: DocumentPreviewProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [percent, setPercent] = useState(100);
  const { stageRef, graphicRef, zoomBy, reset, dragProps } = usePanZoom<HTMLImageElement>({
    onScale: (scale) => setPercent(Math.round(scale * 100)),
    wheelHostRef: frameRef,
  });

  if (content.kind !== "url") return null;

  return (
    <div ref={frameRef} className="relative h-full overflow-hidden bg-sidebar">
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-lg border border-line bg-bg/90 px-1 py-1 text-[12px]">
        <button type="button" className="size-6 rounded hover:bg-hover" aria-label="缩小" onClick={() => zoomBy(1 / BUTTON_STEP)}>
          −
        </button>
        <span className="w-10 text-center tabular-nums text-muted">{percent}%</span>
        <button type="button" className="size-6 rounded hover:bg-hover" aria-label="放大" onClick={() => zoomBy(BUTTON_STEP)}>
          +
        </button>
        <button type="button" className="h-6 rounded px-1.5 text-muted hover:bg-hover hover:text-ink" onClick={() => reset()}>
          适应
        </button>
      </div>
      <div ref={stageRef} className="pan-zoom flex h-full items-center justify-center" onDoubleClick={() => reset()} {...dragProps}>
        <img
          ref={graphicRef}
          src={content.url}
          alt={name}
          draggable={false}
          className="pan-zoom-target max-h-full max-w-full object-contain"
        />
      </div>
    </div>
  );
}
