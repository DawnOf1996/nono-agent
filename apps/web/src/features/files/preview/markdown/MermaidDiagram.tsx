import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { sanitizeMermaidSvg } from "@/features/files/preview/markdown/svg";

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid() {
  mermaidReady ??= import("mermaid").then((module) => {
    module.default.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      htmlLabels: false,
      theme: "neutral",
    });
    return module.default;
  });
  return mermaidReady;
}

let renderSeq = 0;

export function MermaidDiagram({ code }: { code: string }) {
  const reactId = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const source = code.trim();

  useEffect(() => {
    if (!source) return;
    let live = true;
    setError(null);
    void loadMermaid()
      .then(async (mermaid) => {
        renderSeq += 1;
        const rendered = await mermaid.render(`mermaid-${reactId}-${renderSeq}`, source);
        const clean = sanitizeMermaidSvg(rendered.svg);
        if (!clean) throw new Error("图表未通过安全检查");
        if (live) setSvg(clean);
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setSvg(null);
        setError(cause instanceof Error ? cause.message : "无法渲染图表");
      });
    return () => {
      live = false;
    };
  }, [reactId, source]);

  if (!source) return null;
  if (error) {
    return (
      <div className="my-3 overflow-hidden rounded-lg border border-line">
        <p className="border-b border-line bg-[#fdf6e3] px-3 py-1.5 text-[12px] text-[#7a5b12]" title={error}>
          Mermaid 图表无法渲染
        </p>
        <pre className="overflow-auto px-3 py-2 text-[12px] leading-5">{source}</pre>
      </div>
    );
  }
  if (!svg) {
    return <p className="my-3 text-[12px] text-faint">正在渲染图表</p>;
  }
  return (
    <>
      <button
        type="button"
        className="mermaid-diagram my-3 block w-full overflow-auto rounded-lg border border-line bg-white p-3 text-left"
        title="点击放大"
        onClick={() => setOpen(true)}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      {open ? <MermaidLightbox svg={svg} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function diagramSize(markup: string): { width: number; height: number } | null {
  const viewBox = /viewBox=["']\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)\s*["']/i.exec(markup);
  if (!viewBox) return null;
  const width = Number(viewBox[3]);
  const height = Number(viewBox[4]);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  return { width, height };
}

function MermaidLightbox({ svg, onClose }: { svg: string; onClose: () => void }) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [origin, setOrigin] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    const graphic = stage?.querySelector("svg");
    if (!stage || !graphic) return;
    const bounds = graphic.getBoundingClientRect();
    const availableWidth = Math.max(stage.clientWidth - 32, 1);
    const availableHeight = Math.max(stage.clientHeight - 32, 1);
    const width = bounds.width || diagramSize(svg)?.width || 0;
    const height = bounds.height || diagramSize(svg)?.height || 0;
    if (width < 1 || height < 1) return;
    const fit = Math.min(availableWidth / width, availableHeight / height);
    setScale(Math.min(8, Math.max(0.2, fit)));
    setOrigin({ x: 0, y: 0 });
  }, [svg]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-6"
      onClick={onClose}
      onWheel={(event) => {
        event.preventDefault();
        const next = event.deltaY < 0 ? scale * 1.1 : scale / 1.1;
        setScale(Math.min(8, Math.max(0.2, next)));
      }}
    >
      <div
        ref={stageRef}
        className="flex h-[90vh] w-[92vw] cursor-grab items-center justify-center overflow-hidden rounded-xl bg-white p-4 active:cursor-grabbing"
        onClick={(event) => event.stopPropagation()}
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
      >
        <div
          className="mermaid-diagram origin-center"
          style={{ transform: `translate(${origin.x}px, ${origin.y}px) scale(${scale})` }}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
    </div>,
    document.body,
  );
}
