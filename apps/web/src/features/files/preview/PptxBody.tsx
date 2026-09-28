import { useEffect, useRef, useState } from "react";

import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

type PptxViewerInstance = import("@aiden0z/pptx-renderer").PptxViewer;

type LoadState =
  | { status: "loading" }
  | { status: "ready"; count: number }
  | { status: "error"; message: string };

export function PptxBody({ content, name }: DocumentPreviewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<PptxViewerInstance | null>(null);
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [slide, setSlide] = useState(0);
  const [navigating, setNavigating] = useState(false);
  const data = content.kind === "bytes" ? content.data : null;

  useEffect(() => {
    const host = hostRef.current;
    if (data === null || host === null) return;
    const controller = new AbortController();
    setLoad({ status: "loading" });
    setSlide(0);
    void (async () => {
      try {
        const { PptxViewer, RECOMMENDED_ZIP_LIMITS } = await import("@aiden0z/pptx-renderer");
        if (controller.signal.aborted) return;
        const viewer = await PptxViewer.open(data, host, {
          renderMode: "slide",
          fitMode: "contain",
          lazyMedia: true,
          lazySlides: true,
          pdfjs: false,
          signal: controller.signal,
          zipLimits: RECOMMENDED_ZIP_LIMITS,
          onSlideChange: (index) => {
            if (!controller.signal.aborted) setSlide(index);
          },
        });
        if (controller.signal.aborted) {
          viewer.destroy();
          return;
        }
        viewerRef.current = viewer;
        setSlide(viewer.currentSlideIndex);
        setLoad({ status: "ready", count: viewer.slideCount });
      } catch (cause) {
        if (controller.signal.aborted) return;
        try {
          viewerRef.current?.destroy();
        } catch {
          // 初始化中途失败时，实例可能还不能销毁。
        }
        viewerRef.current = null;
        host.innerHTML = "";
        setLoad({ status: "error", message: cause instanceof Error ? cause.message : "无法预览这个演示文稿" });
      }
    })();
    return () => {
      controller.abort();
      try {
        viewerRef.current?.destroy();
      } catch {
        // 已经销毁过。
      }
      viewerRef.current = null;
      host.innerHTML = "";
    };
  }, [data]);

  const navigate = (target: number) => {
    const viewer = viewerRef.current;
    if (viewer === null || load.status !== "ready" || navigating) return;
    const next = Math.max(0, Math.min(load.count - 1, target));
    if (next === slide) return;
    setNavigating(true);
    void viewer
      .goToSlide(next, { behavior: "auto" })
      .then(() => setSlide(viewer.currentSlideIndex))
      .catch((cause: unknown) => {
        setLoad({ status: "error", message: cause instanceof Error ? cause.message : "无法切换幻灯片" });
      })
      .finally(() => setNavigating(false));
  };

  if (data === null) return null;
  const count = load.status === "ready" ? load.count : 0;
  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar" aria-label={name}>
      <div className="flex shrink-0 items-center justify-center gap-2 border-b border-line px-2 py-1">
        <button
          type="button"
          className="h-7 rounded-md px-2 text-[12px] text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
          disabled={load.status !== "ready" || slide <= 0 || navigating}
          onClick={() => navigate(slide - 1)}
        >
          上一页
        </button>
        <span className="min-w-14 text-center text-[12px] text-faint">
          {load.status === "ready" ? `${slide + 1} / ${count}` : "– / –"}
        </span>
        <button
          type="button"
          className="h-7 rounded-md px-2 text-[12px] text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
          disabled={load.status !== "ready" || slide >= count - 1 || navigating}
          onClick={() => navigate(slide + 1)}
        >
          下一页
        </button>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={hostRef} className="h-full w-full overflow-auto" />
        {load.status !== "ready" ? (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-sidebar">
            {load.status === "loading" ? <PreviewLoading label="正在读取幻灯片" /> : null}
            {load.status === "error" ? <p className="px-4 py-6 text-[13px] leading-6 text-danger">{load.message}</p> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
