import { useEffect, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions, RenderingCancelledException, type PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

GlobalWorkerOptions.workerSrc = workerUrl;

function pdfAsset(folder: string): string {
  const base = import.meta.env.BASE_URL;
  const prefix = base.endsWith("/") ? base : `${base}/`;
  return `${prefix}pdfjs/${folder}/`;
}

function PdfCanvas({ pdf, pageNumber, scale }: { pdf: PDFDocumentProxy; pageNumber: number; scale: number }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    let cancelled = false;
    let renderTask: { cancel: () => void } | null = null;
    let lastWidth = 0;
    let generation = 0;

    const paint = () => {
      const width = Math.max(1, Math.floor(wrap.clientWidth * scale));
      if (width === lastWidth) return;
      lastWidth = width;
      const current = ++generation;
      renderTask?.cancel();
      void (async () => {
        const page = await pdf.getPage(pageNumber);
        if (cancelled || current !== generation) return;
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: width / base.width });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        const context = canvas.getContext("2d");
        if (!context) return;
        const task = page.render({
          canvasContext: context,
          viewport,
          transform: [ratio, 0, 0, ratio, 0, 0],
          background: "#ffffff",
        });
        renderTask = task;
        try {
          await task.promise;
        } catch (cause) {
          if (cause instanceof RenderingCancelledException || cancelled) return;
        }
      })();
    };

    paint();
    const observer = new ResizeObserver(paint);
    observer.observe(wrap);
    return () => {
      cancelled = true;
      renderTask?.cancel();
      observer.disconnect();
    };
  }, [pageNumber, pdf, scale]);

  return (
    <div ref={wrapRef} className="w-full">
      <canvas ref={canvasRef} className="mx-auto block bg-white shadow-[0_0_0_1px_var(--color-line)]" />
    </div>
  );
}

function PdfPage({ pdf, pageNumber, scale }: { pdf: PDFDocumentProxy; pageNumber: number; scale: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(pageNumber <= 2);

  useEffect(() => {
    const node = ref.current;
    if (!node || visible) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
      },
      { rootMargin: "600px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  return (
    <div ref={ref} className={visible ? "" : "min-h-[70vh]"}>
      {visible ? <PdfCanvas pdf={pdf} pageNumber={pageNumber} scale={scale} /> : null}
    </div>
  );
}

export function PdfBody({ content }: DocumentPreviewProps) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = content.kind === "bytes" ? content.data : null;
  const frameRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    function onWheel(event: WheelEvent) {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setScale((current) => Math.min(4, Math.max(0.5, event.deltaY < 0 ? current * 1.1 : current / 1.1)));
    }
    frame.addEventListener("wheel", onWheel, { passive: false });
    return () => frame.removeEventListener("wheel", onWheel);
  }, [pdf]);

  useEffect(() => {
    if (data === null) return;
    let live = true;
    setPdf(null);
    setError(null);
    const task = getDocument({
      data: data.slice(),
      cMapUrl: pdfAsset("cmaps"),
      cMapPacked: true,
      standardFontDataUrl: pdfAsset("standard_fonts"),
      isEvalSupported: false,
    });
    void task.promise
      .then((loaded) => {
        if (!live) {
          void loaded.destroy();
          return;
        }
        setPdf(loaded);
      })
      .catch(() => {
        if (live) setError("无法打开这份 PDF");
      });
    return () => {
      live = false;
      void task.destroy();
    };
  }, [data]);

  if (data === null) return null;
  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (!pdf) return <PreviewLoading label="正在打开 PDF" />;
  return (
    <div ref={frameRef} className="flex h-full flex-col bg-sidebar">
      <div className="flex h-9 shrink-0 items-center justify-end gap-1 border-b border-line bg-bg px-2 text-[12px]">
        <button type="button" className="size-6 rounded hover:bg-hover" aria-label="缩小" onClick={() => setScale((value) => Math.max(0.5, value / 1.2))}>
          −
        </button>
        <span className="w-10 text-center tabular-nums text-muted">{Math.round(scale * 100)}%</span>
        <button type="button" className="size-6 rounded hover:bg-hover" aria-label="放大" onClick={() => setScale((value) => Math.min(4, value * 1.2))}>
          +
        </button>
        <button type="button" className="h-6 rounded px-1.5 text-muted hover:bg-hover hover:text-ink" onClick={() => setScale(1)}>
          适应
        </button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        {Array.from({ length: pdf.numPages }, (_, index) => (
          <PdfPage key={index + 1} pdf={pdf} pageNumber={index + 1} scale={scale} />
        ))}
      </div>
    </div>
  );
}
