import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

function diagramSize(markup: string): { width: number; height: number } | null {
  const viewBox = /viewBox=["']\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)\s*["']/i.exec(markup);
  if (!viewBox) return null;
  const width = Number(viewBox[3]);
  const height = Number(viewBox[4]);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  return { width, height };
}

export function DiagramLightbox({
  svg,
  onClose,
  frameClassName = "mermaid-diagram",
}: {
  svg: string;
  onClose: () => void;
  frameClassName?: string;
}) {
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
          className={`${frameClassName} origin-center`}
          style={{ transform: `translate(${origin.x}px, ${origin.y}px) scale(${scale})` }}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
    </div>,
    document.body,
  );
}
