import { useEffect, useRef, useState } from "react";

import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

type LoadState = { status: "loading" } | { status: "ready" } | { status: "error"; message: string };

export function DocxBody({ content, name }: DocumentPreviewProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [zoom, setZoom] = useState(100);
  const data = content.kind === "bytes" ? content.data : null;

  useEffect(() => {
    const wrap = wrapRef.current;
    if (data === null || wrap === null) return;
    let live = true;
    setLoad({ status: "loading" });
    setZoom(100);
    void (async () => {
      try {
        const { renderAsync } = await import("docx-preview");
        await renderAsync(data, wrap, undefined, {
          className: "docx",
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          breakPages: true,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
        });
        if (!live) return;
        const page = wrap.querySelector("section");
        const viewport = viewportRef.current;
        if (page instanceof HTMLElement && viewport && page.offsetWidth > 0) {
          const fit = Math.floor((viewport.clientWidth / page.offsetWidth) * 100);
          setZoom(Math.max(25, Math.min(200, fit)));
        }
        setLoad({ status: "ready" });
      } catch (cause) {
        if (live) setLoad({ status: "error", message: cause instanceof Error ? cause.message : "无法预览这个文档" });
      }
    })();
    return () => {
      live = false;
      wrap.innerHTML = "";
    };
  }, [data]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (viewport === null) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.altKey) return;
      event.preventDefault();
      const delta = event.deltaY < 0 ? 10 : -10;
      setZoom((current) => Math.max(25, Math.min(200, current + delta)));
    };
    viewport.addEventListener("wheel", onWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", onWheel);
  }, []);

  if (data === null) return null;
  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar">
      <div ref={viewportRef} className="relative min-h-0 flex-1 overflow-auto">
        {load.status === "loading" ? (
          <div className="absolute inset-0 z-10">
            <PreviewLoading label="正在转换文档" />
          </div>
        ) : null}
        {load.status === "error" ? <p className="px-4 py-6 text-[13px] leading-6 text-danger">{load.message}</p> : null}
        <div
          ref={wrapRef}
          aria-label={name}
          className={load.status === "error" ? "hidden" : undefined}
          style={{ zoom: zoom / 100 }}
        />
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-line px-3 py-1">
        <span className="shrink-0 text-[11px] text-faint">Alt + 滚轮缩放</span>
        <input
          className="min-w-0 flex-1 accent-ink"
          type="range"
          min={25}
          max={200}
          step={10}
          value={zoom}
          aria-label="缩放"
          onChange={(event) => setZoom(Number(event.currentTarget.value))}
        />
        <span className="w-9 shrink-0 text-right text-[11px] text-faint">{zoom}%</span>
      </div>
    </div>
  );
}
