import { useEffect, useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";

import { usePanZoom } from "@/lib/use-pan-zoom";

const STAGE_PADDING = 32;

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
  const overlayRef = useRef<HTMLDivElement>(null);
  const { stageRef, graphicRef, reset, dragged, dragProps } = usePanZoom({ wheelHostRef: overlayRef });
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  /** 按 viewBox 的自然尺寸排一次版，再按舞台大小取适应倍率。 */
  function fit() {
    const stage = stageRef.current;
    const graphic = graphicRef.current;
    if (!stage || !graphic) return;
    const node = graphic.querySelector("svg");
    const natural = diagramSize(svg);
    if (node && natural) {
      // mermaid 输出的是 width="100%" 加内联 max-width，先按容器宽栅格化再放大，文字会发虚。
      // 灯箱里改成自然尺寸，缩放才有足够像素可采样。
      node.style.width = `${natural.width}px`;
      node.style.height = "auto";
      node.style.maxWidth = "none";
    }
    graphic.style.transform = "none";
    const bounds = graphic.getBoundingClientRect();
    const width = bounds.width || natural?.width || 0;
    const height = bounds.height || natural?.height || 0;
    if (width < 1 || height < 1) return;
    const scale = Math.min(
      (stage.clientWidth - STAGE_PADDING) / width,
      (stage.clientHeight - STAGE_PADDING) / height,
    );
    reset({ scale });
  }

  // 只在换图时重新适应；deps 里刻意不放 fit，否则父组件重渲染会把用户的缩放和位移抹掉。
  useLayoutEffect(fit, [svg, reset]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") closeRef.current();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-6"
      onClick={() => {
        // 从画布拖到背景再松手时，click 会落在两者共同祖先（背景）上，别把这当关闭。
        if (dragged.current) {
          dragged.current = false;
          return;
        }
        onClose();
      }}
    >
      <div
        ref={stageRef}
        className="pan-zoom flex h-[90vh] w-[92vw] items-center justify-center overflow-hidden rounded-xl bg-white p-4"
        title="按住拖动平移 · 滚轮缩放 · 双击适应窗口"
        onClick={(event) => {
          // 在画布内松手时 click 落在画布上，这里顺手把拖拽标记清掉，别留着吞掉下一次点背景。
          dragged.current = false;
          event.stopPropagation();
        }}
        onDoubleClick={fit}
        {...dragProps}
      >
        <div
          ref={graphicRef}
          // shrink-0：SVG 按自然尺寸排版后常常比舞台宽，不能让 flex 把它压回容器宽度，
          // 否则量到的是外框而不是图形，适应倍率和居中都会算错。
          className={`${frameClassName} pan-zoom-target shrink-0`}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
    </div>,
    document.body,
  );
}
