import { Maximize2, X } from "lucide-react";
import { lazy, Suspense } from "react";

import { usePanelStore } from "@/features/files/panel-store";
import { PreviewLoading } from "@/features/files/preview/Loading";

const DiffTab = lazy(() => import("@/features/files/diff/DiffTab").then((module) => ({ default: module.DiffTab })));

/** 变更列表底部的 diff。拖上缘改高度，也可以展开成独立标签。 */
export function ChangePreview() {
  const preview = usePanelStore((state) => state.changePreview);
  const height = usePanelStore((state) => state.previewHeight);
  const setPreviewHeight = usePanelStore((state) => state.setPreviewHeight);
  const clearChangePreview = usePanelStore((state) => state.clearChangePreview);
  const openDiff = usePanelStore((state) => state.openDiff);
  if (!preview) return null;
  return (
    <div className="flex shrink-0 flex-col overflow-hidden border-t border-line" style={{ height }}>
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="调整对比预览高度"
        aria-valuenow={height}
        className="group h-1.5 shrink-0 cursor-row-resize bg-line hover:bg-ink/20"
        onPointerDown={(event) => {
          event.preventDefault();
          const startY = event.clientY;
          const start = height;
          function move(ev: PointerEvent) {
            setPreviewHeight(start + (startY - ev.clientY));
          }
          function end() {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", end);
          }
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", end);
        }}
      />
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-line px-2">
        <span className="min-w-0 flex-1 truncate text-[12px]" title={preview.title}>
          {preview.title}
        </span>
        <button
          type="button"
          className="flex h-6 items-center gap-1 rounded-md px-1.5 text-[12px] text-muted hover:bg-hover hover:text-ink"
          onClick={() => openDiff(preview.target, preview.title)}
        >
          <Maximize2 className="size-3.5" />
          展开
        </button>
        <button
          type="button"
          className="flex size-6 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
          aria-label="关闭对比预览"
          onClick={clearChangePreview}
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <Suspense fallback={<PreviewLoading />}>
          <DiffTab tabId="change-preview" target={preview.target} visible />
        </Suspense>
      </div>
    </div>
  );
}
